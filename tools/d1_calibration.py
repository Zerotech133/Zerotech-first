# -*- coding: utf-8 -*-
"""D1 校准门（2026-10-30）：无 Brier/ECE 不谈加人。

读 decision_outcomes.jsonl 的方向性样本（BUY/SELL + hit），算 Brier/ECE；
样本不足或指标缺失时 can_expand=False。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTCOMES = ROOT / "agent_platform" / "data" / "shadow" / "decision_outcomes.jsonl"
DIRECTIONAL = {"BUY", "SELL", "STRONG_BUY", "STRONG_SELL"}
CONF_FLOOR_N = 30


def load_rows(path=None):
    rows = []
    try:
        for line in Path(path or OUTCOMES).read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001
                pass
    except OSError:
        pass
    return rows


def directional(rows):
    out = []
    for r in rows:
        rec = str(r.get("recommendation") or "").upper()
        hit = r.get("hit")
        conf = r.get("confidence")
        if rec not in DIRECTIONAL or hit is None or not isinstance(conf, (int, float)):
            continue
        out.append((float(conf), int(bool(hit))))
    return out


def brier(pairs):
    return round(sum((p - y) ** 2 for p, y in pairs) / len(pairs), 4) if pairs else None


def ece(pairs, bins=10):
    if not pairs:
        return None
    total = float(len(pairs))
    err = 0.0
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        bucket = [(p, y) for p, y in pairs if (p == 1.0 and i == bins - 1) or lo <= p < hi]
        if not bucket:
            continue
        avg_p = sum(p for p, _ in bucket) / len(bucket)
        avg_y = sum(y for _, y in bucket) / len(bucket)
        err += (len(bucket) / total) * abs(avg_p - avg_y)
    return round(err, 4)


def evaluate(rows, min_n=CONF_FLOOR_N):
    pairs = directional(rows)
    b = brier(pairs)
    e = ece(pairs)
    reasons = []
    if len(pairs) < min_n:
        reasons.append("方向性样本 %d < %d" % (len(pairs), min_n))
    if b is None:
        reasons.append("Brier 不可计算")
    if e is None:
        reasons.append("ECE 不可计算")
    return {
        "schema": "d1_calibration/v1",
        "n_directional": len(pairs),
        "min_n": min_n,
        "brier": b,
        "ece": e,
        "can_expand": not reasons,
        "reasons": reasons,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--min-n", type=int, default=CONF_FLOOR_N)
    args = ap.parse_args(argv)
    rep = evaluate(load_rows(), min_n=args.min_n)
    if args.json or not args.gate:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    if args.gate:
        print("D1_CALIBRATION", "PASS" if rep["can_expand"] else "BLOCKED")
        return 0 if rep["can_expand"] else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
