# -*- coding: utf-8 -*-
"""流动性暴露度对照（2026-11-10，老田指令）。

背景：U0 可交易域审计（chanlun 线）发现 602 笔（8.7%）成交额<0.2 亿的票
撑起全部正超额（剔后 −189bp），1000 万单票=中位成交额 13.9%。
本脚本把 bt10m 线的全部历史实买样本按**买入日成交额**分桶重算，
验证「微型票撑收益」的依赖是否同样存在——两条线共同的结论开关。

口径：
- 腿：v3 现役 + d_legacy 对照，全历史连续段 20231010→20261109，122bp 主口径。
- 分桶：<0.2亿（U0 微型票阈值）/ 0.2~1亿 / 1~5亿 / >5亿（买入日当日成交额）。
- 超额 = pnl − 等权基准同区间收益（与 bt10m_align 同族口径）。
- 1000 万参与率 = 满槽 100 万 / 当日成交额（上限 100%），对照 U0 中位 13.9%。
- amount 列 schema 混杂（部分文件缺列），缺列记 not_joined 并报告覆盖率。
"""
import os
import sys
import json
import math
import statistics

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
sys.path.insert(0, BASE)

import backtest_engine_v2 as eng
import bt10m

D0, D1 = "20231010", "20261109"
COST = 0.0061  # 122bp 主口径
BUCKETS = [(0.0, 0.2e8), (0.2e8, 1e8), (1e8, 5e8), (5e8, float("inf"))]
TAGS = ["<0.2亿", "0.2~1亿", "1~5亿", ">5亿"]
SLOT_10M = 1_000_000  # 1000 万 × 10% 满槽


def trade_pairs(trades):
    """BUY→SELL 配对（与 walkforward 同族）。"""
    q, pairs = {}, []
    for t in sorted(trades, key=lambda x: x["day"]):
        if t["side"] == "BUY":
            q.setdefault(t["stock"], []).append(t["day"])
        elif t["side"] == "SELL" and t.get("pnl") is not None:
            lst = q.get(t["stock"])
            if lst:
                pairs.append({"stock": t["stock"], "bday": lst.pop(0),
                              "sday": t["day"], "pnl": float(t["pnl"]),
                              "how": t.get("how")})
    return pairs


def bench_ret(nav, ks, a, b):
    la = [k for k in ks if k <= a]
    lb = [k for k in ks if k <= b]
    if not la or not lb:
        return None
    return (nav[lb[-1]] / nav[la[-1]] - 1) * 100


def amount_yuan(s, day):
    """买入日成交额（元）；缺列/缺日返回 None。"""
    b = eng._book(s)
    if b is None or "amount" not in b.columns:
        return None
    row = b.loc[b["trade_date"] == day]
    if row.empty:
        return None
    v = row["amount"].iloc[0]
    if not v or v <= 0:
        return None
    return float(v) * 1000.0  # 千元→元


def bucket_of(amt):
    for i, (lo, hi) in enumerate(BUCKETS):
        if lo <= amt < hi:
            return i
    return len(BUCKETS) - 1


def analyze(tag, trades, nav, ks, out):
    pairs = trade_pairs(trades)
    joined, miss = [], 0
    for p in pairs:
        p["amt"] = amount_yuan(p["stock"], p["bday"])
        br = bench_ret(nav, ks, p["bday"], p["sday"])
        p["ex"] = (p["pnl"] - br) if br is not None else None
        if p["amt"] is None or p["ex"] is None:
            miss += 1
        else:
            joined.append(p)

    res = {"n_trades": len(pairs), "n_joined": len(joined), "n_missing": miss,
           "buckets": {}}
    for i, t in enumerate(TAGS):
        xs = [p for p in joined if bucket_of(p["amt"]) == i]
        if not xs:
            res["buckets"][t] = {"n": 0}
            continue
        ex = [p["ex"] for p in xs]
        m = statistics.mean(ex)
        sd = statistics.stdev(ex) or 1e-9
        res["buckets"][t] = {
            "n": len(xs),
            "mean_pnl_pct": round(statistics.mean([p["pnl"] for p in xs]), 2),
            "mean_ex_bp": round(m * 100),
            "t": round(m / (sd / math.sqrt(len(xs))), 2),
            "win_pct": round(100.0 * sum(1 for x in ex if x > 0) / len(xs), 1),
            "amt_min_yi": round(min(p["amt"] for p in xs) / 1e8, 3),
        }

    # 小票 vs 大票贡献（<1亿 阈值）
    small = [p["ex"] for p in joined if p["amt"] < 1e8]
    big = [p["ex"] for p in joined if p["amt"] >= 1e8]
    res["small_lt1yi"] = {
        "n": len(small),
        "mean_ex_bp": round(statistics.mean(small) * 100) if small else None,
        "sum_ex_bp": round(sum(small) * 100) if small else 0,
    }
    res["big_ge1yi"] = {
        "n": len(big),
        "mean_ex_bp": round(statistics.mean(big) * 100) if big else None,
    }

    # 1000 万满槽参与率
    part = [min(1.0, SLOT_10M / p["amt"]) for p in joined]
    res["part_10m"] = {
        "slot_yuan": SLOT_10M,
        "median_pct": round(statistics.median(part) * 100, 2),
        "p90_pct": round(sorted(part)[int(len(part) * 0.9)] * 100, 2),
        "max_pct": round(max(part) * 100, 2),
        "gt_5pct": sum(1 for x in part if x > 0.05),
        "gt_u0median_13p9": sum(1 for x in part if x > 0.139),
    }
    out[tag] = res
    print("\n[%s] %d 笔（join %d，缺 amount %d）" % (tag, len(pairs), len(joined), miss))
    for t in TAGS:
        bkt = res["buckets"][t]
        if bkt.get("n"):
            print("  %-8s n=%3d  笔均pnl %+6.2f%%  超额 %+5dbp  t=%5.2f  胜率 %.0f%%"
                  % (t, bkt["n"], bkt["mean_pnl_pct"], bkt["mean_ex_bp"],
                     bkt["t"], bkt["win_pct"]))
        else:
            print("  %-8s n=0" % t)
    print("  小票(<1亿): n=%d 均超额 %+dbp（贡献合计 %+dbp） | 大票: n=%d 均超额 %+dbp"
          % (res["small_lt1yi"]["n"], res["small_lt1yi"]["mean_ex_bp"] or 0,
             res["small_lt1yi"]["sum_ex_bp"], res["big_ge1yi"]["n"],
             res["big_ge1yi"]["mean_ex_bp"] or 0))
    print("  1000万满槽参与率: 中位 %.2f%%  p90 %.2f%%  最大 %.2f%%  >5%%: %d 笔  >13.9%%(U0中位): %d 笔"
          % (res["part_10m"]["median_pct"], res["part_10m"]["p90_pct"],
             res["part_10m"]["max_pct"], res["part_10m"]["gt_5pct"],
             res["part_10m"]["gt_u0median_13p9"]))


def main():
    print("=== 流动性暴露度对照 %s→%s @%dbp ===" % (D0, D1, COST * 10000))
    nav, _n = bt10m.load_market_bench(D0, D1)
    ks = sorted(nav)
    out = {}
    for leg, preset in (("v3", "v3"), ("d_legacy", "d_legacy")):
        p = {"preset": preset, "d0": D0, "d1": D1, "cost": COST}
        payload, err = eng.run_job(p, "liq_%s" % leg, progress=lambda *a, **k: None)
        if err or not payload:
            print("%s FAIL: %s" % (leg, err))
            continue
        analyze(leg, payload["trades"], nav, ks, out)
    fp = os.path.join(BASE, "reports", "bt10m_liq_exposure.json")
    json.dump(out, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n落盘 %s" % fp)


if __name__ == "__main__":
    main()
