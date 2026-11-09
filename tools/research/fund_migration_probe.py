#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""资金迁移探针 v1 —— 只读诊断 + 内存配对

回答三个问题：
  Q1 amount 字段有多少离群污染（买卖是否守恒）
  Q2 同一席位同日「卖 A + 买 B」这种确定性迁移有多少
  Q3 全市场「A 大额出逃 / B 大额进入」能否配对成资金迁移（假设性推断）

设计铁律：
  - 只读，不写图谱
  - 图上做不了的自连接，拉到内存用 pandas 做
  - 所有阈值显式打印，结论可复算
用法：
  python fund_migration_probe.py --out ../../research/reports/fund_migration_probe.json
"""
import argparse
import json
import os
import sys
import time

import neo4j
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "quant_backtest"))
import kg_schema as S  # noqa: E402


PULL_CYPHER = """
MATCH (t:TradeAction)-[:EXECUTED_BY]->(seat:Seat)
MATCH (t)-[:TARGETS]->(sec:Security)
RETURN t.trade_date AS d,
       t.side       AS side,
       t.amount     AS amt,
       seat.name    AS seat,
       sec.ts_code  AS code,
       sec.name     AS sec_name
"""


def pull(driver):
    t0 = time.time()
    with driver.session() as s:
        recs = s.run(PULL_CYPHER).data()
    print("[pull] %d 行, %.1fs" % (len(recs), time.time() - t0), flush=True)
    df = pd.DataFrame(recs)
    df["amt"] = pd.to_numeric(df["amt"], errors="coerce")
    df["side"] = pd.to_numeric(df["side"], errors="coerce").astype("Int64")
    df = df.dropna(subset=["amt", "side", "d", "code"])
    return df


def q1_outlier(df):
    """amount 离群诊断：按 side 看分位，用 p99.9 与 max 定位污染。"""
    out = {"by_side": {}, "outliers": []}
    for sd, tag in [(1, "buy"), (0, "sell")]:
        g = df.loc[df["side"] == sd, "amt"]
        d = {
            "n": int(g.size),
            "sum_yi": round(float(g.sum()) / 1e8, 2),
            "p50": round(float(g.quantile(0.5)), 0),
            "p99": round(float(g.quantile(0.99)), 0),
            "p999": round(float(g.quantile(0.999)), 0),
            "p9999": round(float(g.quantile(0.9999)), 0),
            "max": round(float(g.max()), 0),
        }
        out["by_side"][tag] = d
    # 逐日守恒
    daily = (df.assign(buy=df["amt"].where(df["side"] == 1, 0.0),
                       sell=df["amt"].where(df["side"] == 0, 0.0))
               .groupby("d")[["buy", "sell"]].sum())
    daily["diff"] = daily["buy"] - daily["sell"]
    daily["dev"] = daily["diff"] / daily["sell"].replace(0, pd.NA).abs()
    out["daily_naive"] = {
        "days": int(len(daily)),
        "median_dev": round(float(daily["dev"].abs().median()), 4),
        "p90_dev": round(float(daily["dev"].abs().quantile(0.9)), 4),
        "days_dev_gt_50pct": int((daily["dev"].abs() > 0.5).sum()),
    }
    # 顶部异常明细
    top = df.nlargest(12, "amt")[["d", "side", "amt", "seat", "code"]]
    for _, r in top.iterrows():
        out["outliers"].append({
            "d": r["d"], "side": int(r["side"]),
            "amt_yi": round(float(r["amt"]) / 1e8, 1),
            "seat": (r["seat"] or "")[:30], "code": r["code"],
        })
    return out, daily


def coverage_asymmetry(df):
    """龙虎榜两侧完整性。真龙虎榜每日每股披露买5席+卖5席，缺侧即数据缺陷。"""
    g = (df.groupby(["d", "code", "side"]).size().unstack(fill_value=0))
    for c in (0, 1):
        if c not in g.columns:
            g[c] = 0
    n = len(g)
    out = {
        "sec_days": int(n),
        "buy_full5": int((g[1] >= 5).sum()),
        "sell_full5": int((g[0] >= 5).sum()),
        "buy_rows": int(g[1].sum()),
        "sell_rows": int(g[0].sum()),
    }
    out["buy_full5_pct"] = round(out["buy_full5"] / max(n, 1) * 100, 1)
    out["sell_full5_pct"] = round(out["sell_full5"] / max(n, 1) * 100, 1)
    out["sell_n_dist"] = {int(k): int(v) for k, v in g[0].value_counts().sort_index().head(6).items()}
    gap = out["buy_rows"] - out["sell_rows"]
    out["row_gap"] = int(gap)
    if out["sell_full5_pct"] < 60:
        out["verdict"] = "卖出侧披露不全，无法单靠龙虎榜识别「谁在出逃」"
    else:
        out["verdict"] = "两侧基本对称，可做主体级配对"
    return out


def clean(df, cap):
    """按 side 各自的 p99.9 设上限，剪掉明显不可能的聚合错误。"""
    keep = pd.Series(True, index=df.index)
    for sd in (0, 1):
        m = df["side"] == sd
        if not m.any():
            continue
        lim = df.loc[m, "amt"].quantile(0.999)
        keep &= ~(m & (df["amt"] > lim))
    dropped = int((~keep).sum())
    return df.loc[keep].copy(), dropped


def q2_migration(df):
    """同一席位同日：卖 A + 买 B。分宽松（同营业部）与严格（同游资标签）。

    先在 (date, seat) 级别筛出真正双向的席位-日，再做标的级配对，
    避免对 42 万行直接 merge（图上自连接已因此超时 400s）。
    """
    cnt = (df.groupby(["d", "seat", "side"]).size().unstack(fill_value=0))
    for c in (0, 1):
        if c not in cnt.columns:
            cnt[c] = 0
    both = cnt.loc[(cnt[0] > 0) & (cnt[1] > 0)]
    res = {"seat_days_with_both": int(len(both)),
           "seat_days_total": int(len(cnt)),
           "both_pct": round(len(both) / max(len(cnt), 1) * 100, 2),
           "raw_pairs": 0, "amt_ratio_p50": None, "samples": []}
    if both.empty:
        return res, None
    idx = pd.MultiIndex.from_arrays([both.index.get_level_values(0),
                                     both.index.get_level_values(1)])
    sub = df.set_index(["d", "seat"]).loc[idx].reset_index()
    buy = sub.loc[sub["side"] == 1, ["d", "seat", "code", "sec_name", "amt"]]
    sell = sub.loc[sub["side"] == 0, ["d", "seat", "code", "sec_name", "amt"]]
    m = buy.merge(sell, on=["d", "seat"], suffixes=("_b", "_s"))
    m = m.loc[m["code_b"] != m["code_s"]]
    res["raw_pairs"] = int(len(m))
    if len(m):
        ratio = m["amt_b"] / m["amt_s"].replace(0, pd.NA)
        res["amt_ratio_p50"] = round(float(ratio.median()), 3)
        res["ratio_within_20pct_pct"] = round(
            float(((ratio > 0.8) & (ratio < 1.2)).mean() * 100), 1)
        m2 = m.assign(gap=(m["amt_b"] - m["amt_s"]).abs() / m["amt_s"].replace(0, pd.NA))
        m2 = m2.dropna(subset=["gap"]).nsmallest(10, "gap")
        for _, r in m2.iterrows():
            res["samples"].append({
                "d": r["d"], "seat": (r["seat"] or "")[:26],
                "buy": r["sec_name_b"], "buy_code": r["code_b"],
                "buy_yi": round(r["amt_b"] / 1e8, 3),
                "sell": r["sec_name_s"], "sell_code": r["code_s"],
                "sell_yi": round(r["amt_s"] / 1e8, 3),
                "gap_pct": round(float(r["gap"]) * 100, 2),
            })
    return res, m


def q3_market_flow(df, top_n=5):
    """全市场：每日净流出的票 vs 净流入的票，看能否按金额配对。"""
    sgn = df["amt"].where(df["side"] == 1, -df["amt"])
    net = (df.assign(net=sgn).groupby(["d", "code"])["net"].sum()
             .reset_index())
    per_day = []
    for d, g in net.groupby("d"):
        outflow = g.loc[g["net"] < 0, "net"].abs().nlargest(top_n)
        inflow = g.loc[g["net"] > 0, "net"].nlargest(top_n)
        if outflow.empty or inflow.empty:
            continue
        o, i = outflow.sum(), inflow.sum()
        per_day.append({
            "d": d,
            "out_yi": round(float(o) / 1e8, 2),
            "in_yi": round(float(i) / 1e8, 2),
            "ratio": round(float(i / o), 3) if o else None,
            "out_n": int(len(outflow)), "in_n": int(len(inflow)),
        })
    s = pd.DataFrame(per_day)
    res = {"days": int(len(s))}
    if len(s):
        res["ratio_p50"] = round(float(s["ratio"].median()), 3)
        res["ratio_p10"] = round(float(s["ratio"].quantile(0.1)), 3)
        res["ratio_p90"] = round(float(s["ratio"].quantile(0.9)), 3)
        res["ratio_within_20pct"] = int(((s["ratio"] > 0.8) & (s["ratio"] < 1.2)).sum())
        res["ratio_within_20pct_pct"] = round(res["ratio_within_20pct"] / len(s) * 100, 1)
        res["sample_days"] = s.tail(6).to_dict("records")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="fund_migration_probe.json")
    ap.add_argument("--top-n", type=int, default=5)
    args = ap.parse_args()

    drv = neo4j.GraphDatabase.driver(S.NEO4J_URI, auth=(S.NEO4J_USER, S.NEO4J_PASSWORD))
    try:
        raw = pull(drv)
    finally:
        drv.close()

    report = {"n_rows": int(len(raw)),
              "date_min": str(raw["d"].min()), "date_max": str(raw["d"].max()),
              "n_days": int(raw["d"].nunique()),
              "n_seats": int(raw["seat"].nunique()),
              "n_secs": int(raw["code"].nunique())}

    print("\n=== Q1 amount 离群诊断 ===", flush=True)
    q1, daily_naive = q1_outlier(raw)
    report["q1_outlier"] = q1
    for tag, d in q1["by_side"].items():
        print("  %-4s n=%7d 合计%12.1f亿  p50=%10.0f  p99=%12.0f  p99.9=%13.0f  max=%15.0f"
              % (tag, d["n"], d["sum_yi"], d["p50"], d["p99"], d["p999"], d["max"]))
    print("  逐日买卖偏离：中位 %.1f%%，p90 %.1f%%，偏离>50%% 的天数 %d/%d"
          % (q1["daily_naive"]["median_dev"] * 100, q1["daily_naive"]["p90_dev"] * 100,
             q1["daily_naive"]["days_dev_gt_50pct"], q1["daily_naive"]["days"]))

    clean_df, dropped = clean(raw, cap=True)
    print("  剪枝：丢弃 %d 行 (%.3f%%)，保留 %d 行" % (dropped, dropped / len(raw) * 100, len(clean_df)))
    report["cleaned"] = {"dropped": dropped, "kept": int(len(clean_df))}

    # 清洗后重算守恒
    q1c, _ = q1_outlier(clean_df)
    report["q1_cleaned"] = q1c
    print("  清洗后：买 %.1f 亿 / 卖 %.1f 亿"
          % (q1c["by_side"]["buy"]["sum_yi"], q1c["by_side"]["sell"]["sum_yi"]))
    print("  清洗后逐日偏离：中位 %.1f%%，偏离>50%% 天数 %d"
          % (q1c["daily_naive"]["median_dev"] * 100, q1c["daily_naive"]["days_dev_gt_50pct"]))

    print("\n=== Q1b 龙虎榜买卖两侧完整性（决定能否配对） ===", flush=True)
    q1b = coverage_asymmetry(raw)
    report["q1b_asymmetry"] = q1b
    print("  标的-日 组合 %d，买入满5席 %d (%.1f%%)，卖出满5席 %d (%.1f%%)"
          % (q1b["sec_days"], q1b["buy_full5"], q1b["buy_full5_pct"],
             q1b["sell_full5"], q1b["sell_full5_pct"]))
    print("  卖出笔数分布: %s" % q1b["sell_n_dist"])
    print("  判定: %s" % q1b["verdict"])

    print("\n=== Q2 同席位同日 卖A+买B ===", flush=True)
    q2, pairs = q2_migration(clean_df)
    report["q2_seat_migration"] = q2
    print("  席位-日 总 %d，其中同日双向 %d (%.1f%%)"
          % (q2["seat_days_total"], q2["seat_days_with_both"], q2["both_pct"]))
    print("  标的级配对 %d 组，买/卖金额比中位 %s"
          % (q2["raw_pairs"],
             q2["amt_ratio_p50"] if q2["amt_ratio_p50"] is not None else "n/a"))
    if "ratio_within_20pct_pct" in q2:
        print("  金额比落 0.8~1.2 的配对占比 %.1f%%" % q2["ratio_within_20pct_pct"])
    for s in q2["samples"][:6]:
        print("   %s %-24s 买 %-10s%7.3f亿 ←→ 卖 %-10s%7.3f亿 差%6.2f%%"
              % (s["d"], s["seat"], s["buy"][:10], s["buy_yi"],
                 s["sell"][:10], s["sell_yi"], s["gap_pct"]))

    print("\n=== Q3 全市场出逃/进入能否配对 ===", flush=True)
    q3 = q3_market_flow(clean_df, top_n=args.top_n)
    report["q3_market_pairing"] = q3
    print("  交易日 %d，top%d 流入/流出 金额比：中位 %.3f（p10 %.3f / p90 %.3f）"
          % (q3["days"], args.top_n, q3.get("ratio_p50", -1),
             q3.get("ratio_p10", -1), q3.get("ratio_p90", -1)))
    if "ratio_within_20pct_pct" in q3:
        print("  比值落在 0.8~1.2（可配对）的交易日 %d 天 = %.1f%%"
              % (q3["ratio_within_20pct"], q3["ratio_within_20pct_pct"]))

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print("\n[done] -> %s (%.1f KB)" % (args.out, os.path.getsize(args.out) / 1024), flush=True)


if __name__ == "__main__":
    main()
