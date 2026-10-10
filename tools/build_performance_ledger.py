# -*- coding: utf-8 -*-
"""绩效账本聚合器（K-STOCK-094，K-STOCK-093 第一步的账本雏形）。

数据流：pick_verdicts.json（766 票判决，win 1/3/5 = D+2 开盘口径）
        → 按 tier / strategy 聚合各窗口 n·hit·avg
        → portal/data/performance_ledger.json（/market 绩效曲线数据源）

后续（P2）：paper.db 实际成交接入后，本脚本增加 per_trade 明细段，
           events trace_id 从 pick 贯穿到 trade。

CLI：python tools/build_performance_ledger.py [--pv 路径] [--out 路径]
"""
import json
import os
import sys
import time
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PV_DEFAULT = os.path.join(ROOT, "portal", "data", "pick_verdicts.json")
OUT_DEFAULT = os.path.join(ROOT, "portal", "data", "performance_ledger.json")
WINDOWS = ("1", "3", "5")


def _agg(rows):
    """rows: [{'win': {w: ret or None}}] → {w: {n, hit, avg}}"""
    out = {}
    for w in WINDOWS:
        rets = [r["win"][w] for r in rows
                if r.get("win") and r["win"].get(w) is not None]
        out[w] = {
            "n": len(rets),
            "hit": (sum(1 for x in rets if x > 0) / len(rets)) if rets else None,
            "avg": (sum(rets) / len(rets)) if rets else None,
        }
    return out


def build(pv_path=PV_DEFAULT, out_path=OUT_DEFAULT):
    pv = json.load(open(pv_path, encoding="utf-8"))
    per_code = pv.get("per_code", {})
    caliber = (pv.get("summary", {}) or {}).get("caliber", {})

    by_tier = defaultdict(list)
    by_strategy = defaultdict(list)
    for code, rec in per_code.items():
        row = dict(rec)
        row["code"] = code
        by_tier[rec.get("tier", "?")].append(row)
        for s in rec.get("strategies", []) or ["none"]:
            by_strategy[s].append(row)

    ledger = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "asof": (pv.get("summary", {}) or {}).get("asof"),
        "caliber": caliber,
        "totals": {"picks": len(per_code), **_agg(list(per_code.values()))},
        "per_tier": {t: {"n": len(rows), **_agg(rows)}
                     for t, rows in sorted(by_tier.items())},
        "per_strategy": {s: {"n": len(rows), **_agg(rows)}
                         for s, rows in sorted(by_strategy.items())},
        "_note": "n<样本窗口数时 hit/avg 为 null 属正常（D+2 开盘口径，"
                 "1/3/5 日窗需对应交易日过去后才有值）。本文件由 "
                 "tools/build_performance_ledger.py 重复跑幂等产出。",
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    json.dump(ledger, open(out_path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    return ledger


def main():
    args = sys.argv[1:]
    pv = args[args.index("--pv") + 1] if "--pv" in args else PV_DEFAULT
    out = args[args.index("--out") + 1] if "--out" in args else OUT_DEFAULT
    led = build(pv, out)
    print("performance_ledger →", out)
    print("totals:", json.dumps(led["totals"], ensure_ascii=False))
    for t, v in led["per_tier"].items():
        print("tier %-3s n=%-4d w1: n=%s hit=%s avg=%s" % (
            t, v["n"], v["1"]["n"],
            None if v["1"]["hit"] is None else "%.1f%%" % (v["1"]["hit"] * 100),
            None if v["1"]["avg"] is None else "%+.2f%%" % (v["1"]["avg"] * 100)))


if __name__ == "__main__":
    main()
