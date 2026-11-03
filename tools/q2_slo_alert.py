#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""组合 SLO 告警闭环 v2（只读评估）。

原能力（保持兼容）：acad1-legion / data-warehouse 单端点 JSON 阈值评估。
WO-Q2-CLOSE-20261104 扩展 lusu-d1-flow：
  - 读取 `tools/q2_probe.py` 的 D1 只读探针结果（8 目标，默认 5 样本/目标）；
  - 按 `docs/standards/INTERFACE_REGISTRY_v1.yaml` 的 D1 接口阈值（draft 目标）
    评估 p95 / error_rate / availability：
      * 探针失败（真实 HTTP 非 2xx/网络错）→ **alert**，不掩盖；
      * 样本数 < MIN_SLO_SAMPLES（窗口不足）→ 每项显式 **not_measured**
        （只报观测值，不伪造窗口 SLO，不判绿）；
      * 样本足够才给 ok/alert 判定；
  - 不改 manifest、不写 active、不启停服务；不改既有 acad1/dw 输出结构。

用法：
  python -X utf8 tools/q2_slo_alert.py --composition lusu-d1-flow --json
  python -X utf8 tools/q2_slo_alert.py --all --json
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import sys
import urllib.request

ENDPOINTS = {
    "acad1-legion": "http://127.0.0.1:8091/api/acad1/pool",
    "data-warehouse": "http://127.0.0.1:8091/api/dw/stats",
}

# D1 SLO 目标（与 docs/standards/INTERFACE_REGISTRY_v1.yaml 的 9 个 D1 接口对齐；
# 目标本身仍为 draft，需 D1 owner 确认后才能当验收基线，见 Q2 证据文档 §六）。
D1_SLO = {
    "d1-research": {"interface": "d1.research.get",
                    "p95_ms": 1500, "error_rate_max": 0.02, "availability_min": 0.99},
    "d1-decisions": {"interface": "d1.decision.list",
                     "p95_ms": 300, "error_rate_max": 0.01, "availability_min": 0.995},
    "d1-handoffs": {"interface": "d1.handoff.list",
                    "p95_ms": 300, "error_rate_max": 0.01, "availability_min": 0.995},
    "d1-meeting": {"interface": "d1.meeting.get",
                   "p95_ms": 300, "error_rate_max": 0.01, "availability_min": 0.995},
    "d1-watch-plans": {"interface": "d1.watch_plans.list",
                       "p95_ms": 300, "error_rate_max": 0.01, "availability_min": 0.995},
    "d1-training-episodes": {"interface": "training.episode.get",
                             "p95_ms": 300, "error_rate_max": 0.01,
                             "availability_min": 0.995},
}

# 窗口 SLO 最小样本：低于该值只输出观测值 + not_measured（不伪造达标/失败）
MIN_SLO_SAMPLES = 20

D1_COMPOSITION = "lusu-d1-flow"


def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _get(url):
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        return {"_error": type(e).__name__ + ":" + str(e)[:160]}


def _p95(values):
    """nearest-rank p95（小样本仅作观测值，不冒充满窗口 SLO）。"""
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, int(math.ceil(0.95 * len(ordered))) - 1)
    return int(ordered[min(index, len(ordered) - 1)])


def _default_d1_probe():
    """延迟 import 同目录 q2_probe，跑 D1 只读探针（5 样本/目标）。"""
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import q2_probe  # noqa: WPS433（同目录工具，延迟导入避免 import 副作用）

    return q2_probe.probe(D1_COMPOSITION)


def _d1_evaluate(probe_report):
    """按 D1_SLO 评估探针样本：不足 not_measured / 失败 alert / 足够才 ok。"""
    by_name = {}
    for check in (probe_report or {}).get("checks") or []:
        by_name[str(check.get("name"))] = check
    checks, alerts = [], []
    not_measured = 0
    for name, target in D1_SLO.items():
        source = by_name.get(name)
        base = {
            "name": name,
            "interface": target["interface"],
            "target": {"p95_ms": target["p95_ms"],
                       "error_rate_max": target["error_rate_max"],
                       "availability_min": target["availability_min"]},
        }
        if source is None:
            alerts.append("probe_missing:" + name)
            checks.append(dict(base, status="alert", ok=False, reason="probe target missing"))
            continue
        statuses = [s for s in (source.get("statuses") or []) if isinstance(s, int)]
        samples = int(source.get("samples") or len(statuses) or 0)
        latencies = [v for v in (source.get("latencies_ms") or []) if isinstance(v, int)]
        ok_samples = sum(1 for s in statuses if 200 <= s < 300)
        failed = max(0, samples - ok_samples)
        if samples == 0:
            alerts.append("probe_no_samples:" + name)
            checks.append(dict(base, status="alert", ok=False, reason="no samples"))
            continue
        observed = {
            "samples": samples,
            "p95_ms": _p95(latencies),
            "error_rate": round(failed / samples, 4),
            "availability": round(ok_samples / samples, 4),
        }
        if failed:
            # 探针失败是真实证据：无论样本多少都 alert（不掩盖，也不因样本少降级）
            alerts.append("probe_failed:%s failed=%d/%d" % (name, failed, samples))
            checks.append(dict(base, status="alert", ok=False,
                               observed=observed, reason="probe failed samples"))
            continue
        if samples < MIN_SLO_SAMPLES:
            not_measured += 1
            checks.append(dict(base, status="not_measured", ok=None,
                               observed=observed,
                               reason="samples=%d < min_window_samples=%d"
                                      % (samples, MIN_SLO_SAMPLES)))
            continue
        breaches = []
        if observed["p95_ms"] is not None and observed["p95_ms"] > target["p95_ms"]:
            breaches.append("p95_ms=%s>%s" % (observed["p95_ms"], target["p95_ms"]))
        if observed["error_rate"] > target["error_rate_max"]:
            breaches.append("error_rate=%s>%s"
                            % (observed["error_rate"], target["error_rate_max"]))
        if observed["availability"] < target["availability_min"]:
            breaches.append("availability=%s<%s"
                            % (observed["availability"], target["availability_min"]))
        if breaches:
            alerts.append("%s: %s" % (name, ",".join(breaches)))
            checks.append(dict(base, status="alert", ok=False,
                               observed=observed, breaches=breaches))
        else:
            checks.append(dict(base, status="ok", ok=True,
                               observed=observed, breaches=[]))
    if alerts:
        slo_status = "alert"
    elif not_measured:
        slo_status = "not_measured"
    else:
        slo_status = "ok"
    return {
        "composition": D1_COMPOSITION,
        "ok": not alerts,
        "checked_at": _now(),
        "slo_status": slo_status,
        "samples_per_target": (probe_report or {}).get("samples_per_target"),
        "min_window_samples": MIN_SLO_SAMPLES,
        "alerts": alerts,
        "not_measured": not_measured,
        "checks": checks,
    }


def evaluate(composition: str, probe_report=None, probe_fn=None) -> dict:
    """评估组合 SLO；lusu-d1-flow 走探针样本评估，其余保持原单端点逻辑。"""
    if composition == D1_COMPOSITION:
        report = probe_report
        if report is None:
            report = (probe_fn or _default_d1_probe)()
        return _d1_evaluate(report)

    data = _get(ENDPOINTS.get(composition, ""))
    alerts, checks = [], []
    if data.get("_error"):
        return {"composition": composition, "ok": False, "checked_at": _now(),
                "alerts": ["probe_failed:" + data["_error"]], "checks": []}
    if composition == "acad1-legion":
        checks = [
            ("freshness_days<=1", data.get("freshness_days"), lambda v: isinstance(v, (int, float)) and v <= 1),
            ("stale=false", data.get("stale"), lambda v: v is False),
            ("pool_total<=30", (data.get("summary") or {}).get("pool_total"), lambda v: isinstance(v, (int, float)) and v <= 30),
            ("data_date_present", data.get("data_date"), lambda v: bool(v)),
        ]
    elif composition == "data-warehouse":
        total = data.get("total") or 0
        reg = data.get("registered") or 0
        exp = data.get("exposed") or 0
        reg_ratio = (reg / total) if total else None
        exp_ratio = (exp / total) if total else None
        checks = [
            ("registered_ratio>=0.95", reg_ratio, lambda v: isinstance(v, (int, float)) and v >= 0.95),
            ("exposed_ratio>=0.80", exp_ratio, lambda v: isinstance(v, (int, float)) and v >= 0.80),
            ("state=ok", data.get("state"), lambda v: v == "ok"),
        ]
    for name, value, fn in checks:
        ok = bool(fn(value))
        if not ok:
            alerts.append("%s (actual=%r)" % (name, value))
    return {"composition": composition, "ok": not alerts, "checked_at": _now(),
            "alerts": alerts,
            "checks": [{"name": n, "value": v, "ok": bool(fn(v))} for n, v, fn in checks]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="组合 SLO 告警评估（只读）")
    ap.add_argument("--composition", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    known = list(ENDPOINTS) + [D1_COMPOSITION]
    names = known if a.all else ([a.composition] if a.composition else [])
    if not names or any(n not in known for n in names):
        print("slo_error: 需要 --composition（%s）或 --all" % "|".join(known), file=sys.stderr)
        return 2
    results = [evaluate(n) for n in names]
    summary = {"compositions": len(results),
               "ok": sum(1 for r in results if r["ok"]),
               "alerts": sum(len(r["alerts"]) for r in results),
               "not_measured": sum(int(r.get("not_measured") or 0) for r in results)}
    out = {"schema": "zerobound/composition_slo_alert/v1", "generated_at": _now(),
           "results": results, "summary": summary}
    print(json.dumps(out, ensure_ascii=False, indent=2) if a.json else
          "\n".join("%s ok=%s status=%s alerts=%s not_measured=%s"
                    % (r["composition"], r["ok"], r.get("slo_status", "-"),
                       r["alerts"], r.get("not_measured", "-")) for r in results))
    return 0 if summary["alerts"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
