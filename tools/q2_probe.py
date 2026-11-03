#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""组合 Q2 健康探针（只读）：只 GET 本机只读端点，不写服务、不改 manifest。

WO-Q2-CLOSE-20261104 扩展：
  - 新增 `lusu-d1-flow` 目标（8091 `/api/health`、`/health`、D1 research/
    decisions/handoffs/meeting/watch-plans/training episodes），**只读 GET**；
    默认每端点 5 样本（真实采样，样本字段 samples/statuses/latencies_ms/
    latency_ms_min|median|max）；
  - `--target d1`（别名：d1/lusu→lusu-d1-flow、acad1→acad1-legion、dw→
    data-warehouse）；输出支持 `--json` / `--markdown`；`--out` 显式落盘证据
    （建议 C:\\Temp，不写仓库生产数据）；
  - 原有 acad1-legion / data-warehouse 保持兼容：默认 1 样本，check 既有字段
    （name/url/ok/status/latency_ms/evidence）不变，仅新增聚合字段。

不启停服务、不写数据、不发起写请求（无 POST）。
"""
from __future__ import annotations

import argparse
import datetime
import json
import statistics
import sys
import time
import urllib.request
from pathlib import Path

TARGETS = {
    "acad1-legion": [
        ("pool", "http://127.0.0.1:8091/api/acad1/pool"),
        ("zhice-health", "http://127.0.0.1:8105/api/v1/health"),
    ],
    "data-warehouse": [
        ("dw-stats", "http://127.0.0.1:8091/api/dw/stats"),
        ("catalog", "http://127.0.0.1:8091/api/dw/search?q=__none__"),
    ],
    # WO-Q2-CLOSE-20261104：D1 只读健康探针（8 目标 × 5 样本）
    "lusu-d1-flow": [
        ("api-health", "http://127.0.0.1:8091/api/health"),
        ("health", "http://127.0.0.1:8091/health"),
        ("d1-research", "http://127.0.0.1:8091/api/d1/research"),
        ("d1-decisions", "http://127.0.0.1:8091/api/d1/decisions"),
        ("d1-handoffs", "http://127.0.0.1:8091/api/d1/handoffs"),
        ("d1-meeting", "http://127.0.0.1:8091/api/d1/meeting"),
        ("d1-watch-plans", "http://127.0.0.1:8091/api/d1/watch-plans"),
        ("d1-training-episodes", "http://127.0.0.1:8091/api/d1/training/episodes"),
    ],
}

# --target 别名（D1 收口：允许 --target d1）
ALIASES = {
    "d1": "lusu-d1-flow",
    "lusu": "lusu-d1-flow",
    "lusu-d1": "lusu-d1-flow",
    "acad1": "acad1-legion",
    "dw": "data-warehouse",
}

# 每端点样本数：D1 按验收要求真实 5 样本；其余沿用单样本（兼容既有调用/门禁耗时）
DEFAULT_SAMPLES = {"lusu-d1-flow": 5}

SCHEMA = "zerobound/composition_health_probe/v1"
BATCH_SCHEMA = "zerobound/composition_health_probe_batch/v1"


def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _get(url, timeout=5.0):
    t = time.time()
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"ok": 200 <= r.status < 300, "status": r.status,
                    "latency_ms": int((time.time() - t) * 1000),
                    "evidence": r.read(2048).decode("utf-8", "replace")[:500]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "latency_ms": int((time.time() - t) * 1000),
                "error": type(e).__name__ + ":" + str(e)[:200]}


def _median(values):
    return int(statistics.median(values)) if values else None


def _aggregate(name, url, runs):
    """多次采样聚合：全 ok 才算 ok；记录样本/状态/延迟分布（不伪造单样本绿）。"""
    ok = bool(runs) and all(bool(r.get("ok")) for r in runs)
    latencies = [r["latency_ms"] for r in runs if isinstance(r.get("latency_ms"), int)]
    first_ok = next((r for r in runs if r.get("ok")), runs[0] if runs else {})
    check = {
        "name": name,
        "url": url,
        "ok": ok,
        "status": first_ok.get("status", 0),
        "statuses": [r.get("status") for r in runs],
        "samples": len(runs),
        "latencies_ms": latencies,
        "latency_ms": _median(latencies),
        "latency_ms_min": min(latencies) if latencies else None,
        "latency_ms_median": _median(latencies),
        "latency_ms_max": max(latencies) if latencies else None,
        "evidence": str(first_ok.get("evidence") or "")[:500],
    }
    errors = [r.get("error") for r in runs if r.get("error")]
    if errors:
        check["errors"] = errors
    return check


def probe(composition, samples=None):
    """按组合探针；D1 默认 5 样本/端点（其余默认 1，兼容既有行为）。"""
    targets = TARGETS.get(composition) or []
    count = samples if samples is not None else DEFAULT_SAMPLES.get(composition, 1)
    count = max(1, int(count))
    checks = []
    for name, url in targets:
        runs = [_get(url) for _ in range(count)]
        checks.append(_aggregate(name, url, runs))
    return {"schema": SCHEMA,
            "composition": composition, "checked_at": _now(),
            "samples_per_target": count,
            "ok": bool(checks) and all(x["ok"] for x in checks), "checks": checks}


def resolve_names(composition="", target="", all_compositions=False):
    """解析 --composition / --target 别名 / --all；返回 (names, error)。"""
    if all_compositions:
        return list(TARGETS), ""
    raw = str(composition or "").strip()
    if not raw and target:
        key = str(target).strip().lower()
        raw = ALIASES.get(key, key)
    if not raw:
        return [], "需要 --composition <name> / --target <d1|acad1|dw> / --all"
    if raw not in TARGETS:
        return [], "未知组合: %s（可选: %s）" % (raw, ", ".join(sorted(TARGETS)))
    return [raw], ""


def render_markdown(batch):
    """批结果 → Markdown（汇报/证据可直接贴）。"""
    lines = [
        "# Q2 健康探针（只读 GET）",
        "",
        "- 生成：%s｜组合数：%d｜ok：%d/%d"
        % (batch.get("generated_at"), batch["summary"]["compositions"],
           batch["summary"]["ok"], batch["summary"]["compositions"]),
        "",
        "| 组合 | 目标 | 样本 | 状态 | 延迟 median/max (ms) | ok |",
        "|---|---|---|---|---|---|",
    ]
    for row in batch.get("results") or []:
        for check in row.get("checks") or []:
            lines.append(
                "| %s | %s | %s | %s | %s / %s | %s |"
                % (row.get("composition"), check.get("name"), check.get("samples"),
                   check.get("status"), check.get("latency_ms_median"),
                   check.get("latency_ms_max"), check.get("ok"))
            )
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="组合 Q2 健康探针（只读）")
    ap.add_argument("--composition", default="")
    ap.add_argument("--target", default="", help="别名：d1/lusu/acad1/dw")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--samples", type=int, default=None, help="每端点样本数（D1 默认 5）")
    ap.add_argument("--out", default="", help="将 JSON 证据写到指定路径（建议 C:\\Temp）")
    a = ap.parse_args(argv)
    names, err = resolve_names(a.composition, a.target, a.all)
    if err:
        print("q2_probe_error: " + err, file=sys.stderr)
        return 2
    rows = [probe(n, samples=a.samples) for n in names]
    out = {"schema": BATCH_SCHEMA,
           "generated_at": _now(), "results": rows,
           "summary": {"compositions": len(rows),
                       "ok": sum(1 for r in rows if r["ok"])}}
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if a.out:
        path = Path(a.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    if a.markdown:
        print(render_markdown(out))
    elif a.json:
        print(text)
    else:
        print("\n".join("%s ok=%s checks=%s"
                        % (r["composition"], r["ok"],
                           [(c["name"], c["status"], c["ok"], c["samples"]) for c in r["checks"]])
                        for r in rows))
    return 0 if all(r["ok"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
