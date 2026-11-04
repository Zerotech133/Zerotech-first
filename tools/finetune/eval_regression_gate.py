# -*- coding: utf-8 -*-
"""评测回归门禁：读取 bailian_eval 40 题结果，按 W13/v4d 阈值判定；可对比 baseline 防退化。

用法：
  python tools/finetune/eval_regression_gate.py --payload research/reports/bailian_eval_20261104.json
  python tools/finetune/eval_regression_gate.py --payload new.json --baseline old.json --tolerance 0.02
退出码：0=通过；1=未达标或退化。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(__file__).resolve().parents[2]

THRESHOLDS = {
    "citation_accuracy": 0.88,
    "groundedness": 0.60,
    "correct_refusal_rate": 0.80,
    "overclaim_rate": 0.05,
    "length_anomaly_rate": 0.10,
}
HIGHER_BETTER = {"citation_accuracy", "groundedness", "correct_refusal_rate"}


def evaluate(payload: dict, baseline: dict | None = None, tolerance: float = 0.02) -> dict:
    agg = payload.get("aggregate") or {}
    checks = []
    ok = True
    for metric, target in THRESHOLDS.items():
        cur = agg.get(metric)
        passed = cur is not None and (cur >= target if metric in HIGHER_BETTER else cur <= target)
        checks.append({"metric": metric, "current": cur, "target": target,
                       "pass": passed, "kind": "threshold"})
        ok = ok and passed
    if baseline:
        base_agg = baseline.get("aggregate") or {}
        for metric in THRESHOLDS:
            cur, base = agg.get(metric), base_agg.get(metric)
            if cur is None or base is None:
                continue
            delta = round(cur - base, 4)
            degraded = (delta < -tolerance) if metric in HIGHER_BETTER else (delta > tolerance)
            checks.append({"metric": metric, "current": cur, "baseline": base, "delta": delta,
                           "tolerance": tolerance, "pass": not degraded, "kind": "regression"})
            ok = ok and not degraded
    return {"ok": ok, "checks": checks, "aggregate": agg,
            "baseline_aggregate": (baseline or {}).get("aggregate")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="评测回归门禁（bailian_eval 40 题）")
    ap.add_argument("--payload", required=True, help="新评测 JSON")
    ap.add_argument("--baseline", default="", help="对比基线 JSON（可选）")
    ap.add_argument("--tolerance", type=float, default=0.02, help="退化容忍度（默认 0.02）")
    ap.add_argument("--json-out", default="", help="结果写入 JSON（可选）")
    args = ap.parse_args(argv)
    payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8")) if args.baseline else None
    result = evaluate(payload, baseline, args.tolerance)
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"ok": result["ok"], "checks": result["checks"]}, ensure_ascii=False, indent=1))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
