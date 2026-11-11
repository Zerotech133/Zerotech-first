# -*- coding: utf-8 -*-
"""诊断：三买买入时，价格相对个股 MA20 在哪里？这些位置与后续收益有关吗？

为什么必须量这一条：
  老田要的「趋势还在就不砍（跌破 20 日均线才走）」默认了一个前提——
  买入时价格在均线之上（趋势成立）。若不成立，这条退出等于买入次日就砍。

产出并入 reports/bt10m_trend_1y.json 的 "ma_diag" 键。
用法：python bt10m_ma_diag.py
"""
import json
import os
import statistics
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bt10m_sweep as SW  # noqa: E402
import bt10m_verify as V  # noqa: E402
import backtest_engine_v2 as eng  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE, "reports", "bt10m_trend_1y.json")

BUCKETS = [(-9.9, -5.0, "≤−5%"), (-5.0, -2.0, "−5~−2%"), (-2.0, 0.0, "−2~0%"),
           (0.0, 2.0, "0~2%"), (2.0, 9.9, ">2%")]


def run(tag, d0, d1):
    p = dict(SW.BASE_PARAMS)
    # D 口径：不加任何入场过滤，看原始买点分布
    p.update({"max_ext_pct": 0.03, "three_fail_exit": False, "holding_max_days": 20,
              "trail_activate": 0.05, "trail_drawdown": 0.035,
              "d0": d0, "d1": d1, "cost": 0.0015})
    payload, err = eng.run_job(p, "ma_diag_%s" % tag, progress=lambda *a, **k: None)
    if err or not payload:
        return {"err": err}

    # FIFO 配对出每笔的收益
    q, pairs = {}, []
    for t in payload["trades"]:
        if t["side"] == "BUY":
            q.setdefault(t["stock"], []).append(t["day"])
        elif t["side"] == "SELL":
            lst = q.get(t["stock"])
            if lst:
                pairs.append((t["stock"], lst.pop(0), t["day"], t.get("pnl")))

    dist, bucket = [], {b[2]: [] for b in BUCKETS}
    for st, bd, sd, pnl in pairs:
        o = eng.px(st, bd, "open")
        m = eng.ma_at(st, bd, 20)
        if o is None or m is None:
            continue
        rel = (o / m - 1) * 100
        dist.append(rel)
        if pnl is None:
            continue
        for lo, hi, lab in BUCKETS:
            if lo < rel <= hi:
                bucket[lab].append(float(pnl))
                break

    return {
        "n": len(dist),
        "below_pct": round(100.0 * sum(1 for x in dist if x <= 0) / len(dist), 1),
        "median": round(statistics.median(dist), 2),
        "mean": round(statistics.mean(dist), 2),
        "p10": round(sorted(dist)[len(dist) // 10], 2),
        "buckets": {k: {"n": len(v), "avg_pnl": round(statistics.mean(v), 2) if v else None}
                    for k, v in bucket.items()},
    }


def main():
    d = json.load(open(SRC, encoding="utf-8"))
    out = {}
    for tag, d0, d1, _ in V.WINDOWS:
        out[tag] = run(tag, d0, d1)
        r = out[tag]
        print("%-4s 买入 %d 笔 · 价格在 MA20 下方 %.1f%% · 中位 %+.2f%% · 10 分位 %+.2f%%"
              % (tag, r["n"], r["below_pct"], r["median"], r["p10"]))
        for k, v in r["buckets"].items():
            print("      %-8s n=%3d 均笔 %s" % (k, v["n"],
                  ("%+.2f%%" % v["avg_pnl"]) if v["avg_pnl"] is not None else "—"))
    d["ma_diag"] = out
    json.dump(d, open(SRC, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("merged -> %s" % SRC)


if __name__ == "__main__":
    main()
