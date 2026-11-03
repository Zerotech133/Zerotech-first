#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""组合 SLO 告警闭环 v2（只读评估）。

原能力（保持兼容）：acad1-legion / data-warehouse 单端点 JSON 阈值评估。
WO-ACAD1-PROBE-20261104 扩展 acad1-legion（司马徽军团接口）：
  - 读取 `tools/q2_probe.py` 的 acad1-legion 探针结果（4 公开端点 + 6 预期 401 端点）；
  - **只对未登录应 200 的公开端点算样本**（pool/zhice-health/shuijing-page/
    hermes-acad1-comparison）；其中仅 `acad1.pool.get` 在
    `docs/standards/INTERFACE_REGISTRY_v1.yaml` 有 SLO 目标（p95/error/availability），
    其余公开端点无 registry SLO 目标 → 显式 `not_measured`（只报观测值，不伪造目标）；
  - 6 个 `acad1-research` 端点未登录 **401 是预期鉴权语义** → 每项显式
    `auth_required` + `not_measured`，**不判失败、不伪造 200**；若返回 2xx（鉴权门缺失）
    或网络/5xx 则 alert（真实证据，不掩盖）；
  - 旧 acad1 的 4 条数据 SLA 观测（freshness_days/stale/pool_total/data_date）保留为
    `interface_checks`/`interface_alerts`：**独立于 SLO 窗口判定**，显式上报但不混入
    `slo_status`（数据口径归 ACAD1/manifest SLA，见探针文档）。
- D1 评估保持原样（样本不足 not_measured / 失败 alert / 足够 ok·alert）。

原 WO-Q2-CLOSE-20261104 扩展 lusu-d1-flow：
  - 读取 `tools/q2_probe.py` 的 D1 只读探针结果（8 目标，默认 5 样本/目标）；
  - 按 `docs/standards/INTERFACE_REGISTRY_v1.yaml` 的 D1 接口阈值（draft 目标）
    评估 p95 / error_rate / availability：
      * 探针失败（真实 HTTP 非 2xx/网络错）→ **alert**，不掩盖；
      * 样本数 < MIN_SLO_SAMPLES（窗口不足）→ 每项显式 **not_measured**
        （只报观测值，不伪造窗口 SLO，不判绿）；
      * 样本足够才给 ok/alert 判定；
  - 不改 manifest、不写 active、不启停服务；不改既有 dw 输出结构。

用法：
  python -X utf8 tools/q2_slo_alert.py --composition acad1-legion --json
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

# WO-ACAD1-PROBE-20261104：acad1-legion 接口分组
ACAD1_COMPOSITION = "acad1-legion"
ACAD1_POOL_URL = "http://127.0.0.1:8091/api/acad1/pool"
# 公开端点名（与 q2_probe.TARGETS["acad1-legion"] 对齐）；未登录应 2xx
ACAD1_PUBLIC_TARGETS = [
    "pool",
    "zhice-health",
    "shuijing-page",
    "hermes-acad1-comparison",
]
# 预期未登录 401 的端点（与 q2_probe.AUTH_TARGETS["acad1-legion"] 对齐）
ACAD1_AUTH_TARGETS = [
    "acad1-research-pool",
    "acad1-research-stats",
    "acad1-research-reviews",
    "acad1-research-decisions",
    "acad1-research-evidence",
    "acad1-research-calendar",
]
# 公开端点 SLO 目标：仅 registry 有登记的 acad1.pool.get（slo_class=read_fast, status=draft）；
# 其余 3 个公开端点 registry 无 SLO 目标 → 显式 not_measured（不自行发明目标）。
ACAD1_SLO = {
    "pool": {"interface": "acad1.pool.get",
             "p95_ms": 300, "error_rate_max": 0.01, "availability_min": 0.995},
}


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
    import q2_probe  # 同目录工具，延迟导入避免 import 副作用

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


def _default_acad1_probe(samples=None):
    """延迟 import 同目录 q2_probe，跑 acad1-legion 只读探针（4 公开 + 6 预期 401）。"""
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import q2_probe  # 同目录工具，延迟导入避免 import 副作用

    return q2_probe.probe(ACAD1_COMPOSITION, samples=samples)


def _acad1_interface_checks(data):
    """acad1 数据 SLA 观测（独立于 SLO 窗口判定）：返回 (checks, alerts)。

    诚实语义：这些是 manifest SLA（freshness_days<=1 / stale=false / pool_total<=30 /
    data_date 存在）的实时观测；不混入 SLO 的 ok/slo_status，只在 interface_alerts 显式上报。
    """
    if not isinstance(data, dict) or data.get("_error"):
        error = str((data or {}).get("_error") or "no payload")[:160]
        return [], ["interface_data_unavailable:" + error]
    rows = [
        ("freshness_days<=1", data.get("freshness_days"),
         lambda v: isinstance(v, (int, float)) and v <= 1),
        ("stale=false", data.get("stale"), lambda v: v is False),
        ("pool_total<=30", (data.get("summary") or {}).get("pool_total"),
         lambda v: isinstance(v, (int, float)) and v <= 30),
        ("data_date_present", data.get("data_date"), lambda v: bool(v)),
    ]
    checks, alerts = [], []
    for name, value, fn in rows:
        ok = bool(fn(value))
        checks.append({"name": name, "value": value, "ok": ok})
        if not ok:
            alerts.append("%s (actual=%r)" % (name, value))
    return checks, alerts


def _acad1_public_check(source, name):
    """单个公开端点：失败 alert；有 registry SLO 且样本足够才判定，否则显式 not_measured。"""
    base = {"name": name, "role": "public"}
    target = ACAD1_SLO.get(name)
    if target is not None:
        base["interface"] = target["interface"]
        base["target"] = {"p95_ms": target["p95_ms"],
                          "error_rate_max": target["error_rate_max"],
                          "availability_min": target["availability_min"]}
    if source is None:
        return dict(base, status="alert", ok=False,
                    reason="probe target missing"), "probe_missing:" + name
    statuses = [s for s in (source.get("statuses") or []) if isinstance(s, int)]
    samples = int(source.get("samples") or len(statuses) or 0)
    latencies = [v for v in (source.get("latencies_ms") or []) if isinstance(v, int)]
    ok_samples = sum(1 for s in statuses if 200 <= s < 300)
    failed = max(0, samples - ok_samples)
    if samples == 0:
        return dict(base, status="alert", ok=False,
                    reason="no samples"), "probe_no_samples:" + name
    observed = {"samples": samples, "p95_ms": _p95(latencies),
                "error_rate": round(failed / samples, 4),
                "availability": round(ok_samples / samples, 4)}
    if failed:
        return (dict(base, status="alert", ok=False, observed=observed,
                     reason="probe failed samples"),
                "probe_failed:%s failed=%d/%d" % (name, failed, samples))
    if target is None:
        return (dict(base, status="not_measured", ok=None, observed=observed,
                     reason="no registry SLO target; observation only"), None)
    if samples < MIN_SLO_SAMPLES:
        return (dict(base, status="not_measured", ok=None, observed=observed,
                     reason="samples=%d < min_window_samples=%d"
                            % (samples, MIN_SLO_SAMPLES)), None)
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
        return (dict(base, status="alert", ok=False, observed=observed,
                     breaches=breaches), "%s: %s" % (name, ",".join(breaches)))
    return dict(base, status="ok", ok=True, observed=observed, breaches=[]), None


def _acad1_auth_check(source, name):
    """预期 401 端点：全 401=预期鉴权语义（not_measured）；2xx=鉴权门缺失 alert；不伪造 200。"""
    base = {"name": name, "role": "auth_required", "expected_status": 401}
    if source is None:
        return dict(base, status="alert", ok=False,
                    reason="probe target missing"), "probe_missing:" + name
    statuses = [s for s in (source.get("statuses") or []) if isinstance(s, int)]
    samples = int(source.get("samples") or len(statuses) or 0)
    if samples == 0:
        return dict(base, status="alert", ok=False,
                    reason="no samples"), "probe_no_samples:" + name
    unexpected = [s for s in statuses if s != 401]
    two_xx = [s for s in unexpected if 200 <= s < 300]
    if two_xx:
        return (dict(base, status="alert", ok=False, statuses=statuses,
                     reason="unauth endpoint returned 2xx — auth gate missing/regressed"),
                "auth_gate_missing:%s statuses=%s (expected 401 auth_required)"
                % (name, statuses))
    if unexpected:
        return (dict(base, status="alert", ok=False, statuses=statuses,
                     reason="unexpected statuses"),
                "probe_unexpected_status:%s statuses=%s" % (name, statuses))
    return (dict(base, status="auth_required", ok=None, statuses=statuses,
                 reason="unauth 401 observed; login required (not measured)"), None)


def _acad1_evaluate(probe_report, interface_payload=None):
    """acad1-legion：公开 200 端点算样本 + 6 个 401 端点显式 auth_required/not_measured。"""
    by_name = {}
    for check in (probe_report or {}).get("checks") or []:
        by_name[str(check.get("name"))] = check
    checks, alerts = [], []
    not_measured = 0
    for name in ACAD1_PUBLIC_TARGETS:
        check, alert = _acad1_public_check(by_name.get(name), name)
        checks.append(check)
        if alert:
            alerts.append(alert)
        elif check.get("status") == "not_measured":
            not_measured += 1
    auth_present = 0
    for name in ACAD1_AUTH_TARGETS:
        check, alert = _acad1_auth_check(by_name.get(name), name)
        checks.append(check)
        if alert:
            alerts.append(alert)
        else:
            auth_present += 1
            not_measured += 1
    interface_checks, interface_alerts = _acad1_interface_checks(interface_payload)
    if alerts:
        slo_status = "alert"
    elif not_measured:
        slo_status = "not_measured"
    else:
        slo_status = "ok"
    return {
        "composition": ACAD1_COMPOSITION,
        "ok": not alerts,
        "checked_at": _now(),
        "slo_status": slo_status,
        "samples_per_target": (probe_report or {}).get("samples_per_target"),
        "min_window_samples": MIN_SLO_SAMPLES,
        "alerts": alerts,
        "not_measured": not_measured,
        "auth_required": auth_present,
        "auth_required_targets": list(ACAD1_AUTH_TARGETS),
        "checks": checks,
        "interface_checks": interface_checks,
        "interface_alerts": interface_alerts,
        "interface_ok": not interface_alerts,
    }


_SENTINEL = object()


def evaluate(composition: str, probe_report=None, probe_fn=None, samples=None,
             interface_payload=_SENTINEL) -> dict:
    """评估组合 SLO；d1/acad1 走探针样本评估，data-warehouse 保持原单端点逻辑。"""
    if composition == D1_COMPOSITION:
        report = probe_report
        if report is None:
            report = (probe_fn or _default_d1_probe)()
        return _d1_evaluate(report)

    if composition == ACAD1_COMPOSITION:
        report = probe_report
        if report is None:
            if probe_fn is not None:
                report = probe_fn()
            else:
                report = _default_acad1_probe(samples)
        if interface_payload is _SENTINEL:
            # 真实运行才额外读一次 pool（SLA 观测）；测试注入 probe_report 时默认不联网
            interface_payload = _get(ACAD1_POOL_URL) if probe_report is None else None
        return _acad1_evaluate(report, interface_payload)

    data = _get(ENDPOINTS.get(composition, ""))
    alerts, checks = [], []
    if data.get("_error"):
        return {"composition": composition, "ok": False, "checked_at": _now(),
                "alerts": ["probe_failed:" + data["_error"]], "checks": []}
    if composition == "data-warehouse":
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
    ap.add_argument("--samples", type=int, default=None,
                    help="acad1 公开端点探针样本数（默认 1；窗口 SLO 需 >=%d）"
                         % MIN_SLO_SAMPLES)
    a = ap.parse_args(argv)
    known = list(ENDPOINTS) + [D1_COMPOSITION]
    names = known if a.all else ([a.composition] if a.composition else [])
    if not names or any(n not in known for n in names):
        print("slo_error: 需要 --composition（%s）或 --all" % "|".join(known), file=sys.stderr)
        return 2
    results = [evaluate(n, samples=a.samples) for n in names]
    summary = {"compositions": len(results),
               "ok": sum(1 for r in results if r["ok"]),
               "alerts": sum(len(r["alerts"]) for r in results),
               "not_measured": sum(int(r.get("not_measured") or 0) for r in results),
               "auth_required": sum(int(r.get("auth_required") or 0) for r in results),
               "interface_alerts": sum(len(r.get("interface_alerts") or []) for r in results)}
    out = {"schema": "zerobound/composition_slo_alert/v1", "generated_at": _now(),
           "results": results, "summary": summary}
    print(json.dumps(out, ensure_ascii=False, indent=2) if a.json else
          "\n".join("%s ok=%s status=%s alerts=%s not_measured=%s auth_required=%s "
                    "interface_alerts=%s"
                    % (r["composition"], r["ok"], r.get("slo_status", "-"),
                       r["alerts"], r.get("not_measured", "-"),
                       r.get("auth_required", "-"),
                       r.get("interface_alerts", "-")) for r in results))
    return 0 if summary["alerts"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
