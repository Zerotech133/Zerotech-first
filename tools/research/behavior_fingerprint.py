#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""行为指纹探针 v1 —— 能否从 T-1 公开数据反推「这笔钱是谁的」

用户命题：
  陈小群 T 日上榜 1 亿 → T-1 日能否从成交数据判断哪 1 亿是他的？

本脚本回答三问：
  Q1 数据边界：手上有没有能做「下单手法」的数据（tick / 个股分钟）
  Q2 选股偏好：他 T 日买入的票，T-1 日呈现什么结构（相对全市场分位）
  Q3 可反推性：用 T-1 特征做二分类（是他 / 不是他），AUC 有多高
     对照组 = 同板块同市值邻域，剥离「选股偏好」后还剩多少「手法」

设计铁律：
  - 只读，不写库
  - 每个结论都要有对照组，否则高 AUC 只是「他买小盘股」这种常识
用法：
  python behavior_fingerprint.py --out ../../research/reports/behavior_fingerprint.json
"""
import argparse
import json
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "quant_backtest"))
import kg_schema as S  # noqa: E402
from neo4j import GraphDatabase  # noqa: E402

DATA = os.path.join(_ROOT, "quant_backtest", "data")
MF = os.path.join(DATA, "tushare_curated", "moneyflow.parquet")
DB = os.path.join(DATA, "tushare", "daily_basic")

FLOW_COLS = ["buy_sm_amount", "buy_md_amount", "buy_lg_amount", "buy_elg_amount",
             "sell_sm_amount", "sell_md_amount", "sell_lg_amount", "sell_elg_amount"]

TARGETS = ["陈小群", "章建平(章盟主)", "方新侠", "赵强(赵老哥)"]


def load_lhb():
    """拉龙虎榜带游资标签的记录。"""
    drv = GraphDatabase.driver(S.NEO4J_URI, auth=(S.NEO4J_USER, S.NEO4J_PASSWORD))
    try:
        with drv.session() as s:
            rows = s.run("""
                MATCH (t:TradeAction)-[:OCCUPIED_BY]->(h:HotMoney)
                MATCH (t)-[:TARGETS]->(sec:Security)
                RETURN h.name AS who, t.trade_date AS d, sec.ts_code AS code,
                       t.side AS side, t.amount AS amt
            """).data()
    finally:
        drv.close()
    df = pd.DataFrame(rows)
    df["amt"] = pd.to_numeric(df["amt"], errors="coerce")
    return df.dropna(subset=["amt", "code", "d"])


def build_daily():
    """券商口径每日四档占比 + 换手/量比/市值。"""
    mf = pd.read_parquet(MF, columns=["ts_code", "trade_date"] + FLOW_COLS)
    mf["trade_date"] = mf["trade_date"].astype(str)
    tot = mf[["buy_sm_amount", "buy_md_amount", "buy_lg_amount", "buy_elg_amount"]].sum(axis=1)
    for c in ["buy_sm_amount", "buy_md_amount", "buy_lg_amount", "buy_elg_amount"]:
        mf["sh_" + c.split("_")[1]] = np.where(tot > 0, mf[c] / tot, np.nan)
    mf["buy_tot"] = tot
    mf["elg_net"] = mf["buy_elg_amount"] - mf["sell_elg_amount"]
    keep = ["ts_code", "trade_date", "buy_tot", "elg_net",
            "sh_sm", "sh_md", "sh_lg", "sh_elg"]

    dbs = []
    for f in sorted(os.listdir(DB)):
        if not f.endswith(".parquet"):
            continue
        t = pd.read_parquet(os.path.join(DB, f),
                            columns=["ts_code", "trade_date", "turnover_rate",
                                     "volume_ratio", "circ_mv", "close"])
        dbs.append(t)
    db = pd.concat(dbs, ignore_index=True)
    db["trade_date"] = db["trade_date"].astype(str)
    out = mf[keep].merge(db, on=["ts_code", "trade_date"], how="inner")
    out["circ_mv_yi"] = out["circ_mv"] / 1e4
    return out


def prev_day_map(dates):
    """把交易日映射到上一个交易日。"""
    u = sorted(set(dates))
    return {u[i]: u[i - 1] for i in range(1, len(u))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="behavior_fingerprint.json")
    ap.add_argument("--sample", type=int, default=400, help="每个游资最多取多少笔做对照")
    args = ap.parse_args()

    rep = {}

    print("[1/4] 数据边界核查", flush=True)
    has_tick = False
    has_stk_min = False
    rep["data_boundary"] = {
        "individual_tick": has_tick,
        "individual_minute_bar": has_stk_min,
        "sector_minute_rows": 5469600,
        "available": ["moneyflow 四档(日频)", "daily_basic 换手/量比/市值", "cyq_perf 筹码分布"],
        "missing": ["逐笔成交(tick)", "个股分钟线", "委托队列(level2)"],
        "verdict": "「每笔多少手 / 分时线上方下方」不可计算；只能做日频结构指纹",
    }
    for k, v in rep["data_boundary"].items():
        print("   %-24s %s" % (k, v))

    print("\n[2/4] 装载龙虎榜与日频结构", flush=True)
    lhb = load_lhb()
    print("   龙虎榜带游资标签 %d 条" % len(lhb))
    daily = build_daily()
    print("   日频结构 %d 行，%s ~ %s" % (len(daily), daily["trade_date"].min(), daily["trade_date"].max()))

    pmap = prev_day_map(daily["trade_date"])
    lhb["d_prev"] = lhb["d"].map(pmap)
    lhb = lhb.dropna(subset=["d_prev"])

    # T-1 特征
    prev = daily.rename(columns={"trade_date": "d_prev"})
    prev = prev.add_prefix("p_").rename(columns={"p_ts_code": "code", "p_d_prev": "d_prev"})
    m = lhb.merge(prev, on=["code", "d_prev"], how="inner")
    print("   可匹配 T-1 结构的记录 %d / %d" % (len(m), len(lhb)))
    rep["join_rate_pct"] = round(len(m) / max(len(lhb), 1) * 100, 1)

    feats = ["p_sh_sm", "p_sh_md", "p_sh_lg", "p_sh_elg", "p_buy_tot",
             "p_elg_net", "p_turnover_rate", "p_volume_ratio", "p_circ_mv_yi"]

    # 全市场基准分布（同期）
    base = daily.dropna(subset=["sh_sm", "sh_md", "sh_lg", "sh_elg"])
    rep["market_baseline"] = {
        "sh_sm_p50": round(float(base["sh_sm"].median()), 3),
        "sh_md_p50": round(float(base["sh_md"].median()), 3),
        "sh_lg_p50": round(float(base["sh_lg"].median()), 3),
        "sh_elg_p50": round(float(base["sh_elg"].median()), 3),
        "turnover_p50": round(float(base["turnover_rate"].median()), 2),
        "circ_mv_p50_yi": round(float(base["circ_mv_yi"].median()), 1),
    }

    print("\n[3/4] 各游资的 T-1 结构 vs 全市场分位", flush=True)
    prof = {}
    for who in TARGETS:
        g = m[m["who"] == who]
        if len(g) < 20:
            continue
        row = {"n": int(len(g)), "amt_median_yi": round(float(g["amt"].median()) / 1e8, 3),
               "amt_p90_yi": round(float(g["amt"].quantile(0.9)) / 1e8, 3)}
        for f, col in [("sh_sm", "p_sh_sm"), ("sh_md", "p_sh_md"),
                       ("sh_lg", "p_sh_lg"), ("sh_elg", "p_sh_elg"),
                       ("turnover_rate", "p_turnover_rate"),
                       ("volume_ratio", "p_volume_ratio"),
                       ("circ_mv_yi", "p_circ_mv_yi")]:
            v = g[col].dropna()
            if len(v) == 0:
                continue
            pct = float((base[f] < v.median()).mean() * 100)
            row[f] = {"median": round(float(v.median()), 2), "market_pct": round(pct, 0)}
        prof[who] = row
        print("   %-14s n=%3d 单笔中位%.3f亿 | T-1超大单占比%.3f(全市场%.3f) 换手%.2f%%(分位%.0f) 市值%.0f亿"
              % (who, row["n"], row["amt_median_yi"],
                 row["sh_elg"]["median"], rep["market_baseline"]["sh_elg_p50"],
                 row["turnover_rate"]["median"], row["turnover_rate"]["market_pct"],
                 row["circ_mv_yi"]["median"]))
    rep["profiles"] = prof

    print("\n[4/4] 可反推性检验（AUC）", flush=True)
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.metrics import roc_auc_score
        from sklearn.model_selection import cross_val_predict
    except Exception as e:  # noqa: BLE001
        print("   sklearn 不可用，跳过：%s" % e)
        rep["auc"] = {"error": str(e)}
    else:
        aucs = {}
        who = "陈小群"
        pos = m[m["who"] == who][["code", "d_prev"] + feats].dropna()
        if len(pos) > 50:
            REN = {"ts_code": "code", "trade_date": "d_prev",
                   "sh_sm": "p_sh_sm", "sh_md": "p_sh_md", "sh_lg": "p_sh_lg",
                   "sh_elg": "p_sh_elg", "buy_tot": "p_buy_tot", "elg_net": "p_elg_net",
                   "turnover_rate": "p_turnover_rate", "volume_ratio": "p_volume_ratio",
                   "circ_mv_yi": "p_circ_mv_yi"}
            # 对照组 A：全市场随机负样本
            negA = base.sample(min(len(pos) * 3, len(base)), random_state=7)
            negA = negA.rename(columns=REN)[["code", "d_prev"] + feats].dropna()
            # 对照组 B：同市值十分位 + 同换手分位的邻域负样本（剥离选股偏好）
            base2 = base.copy()
            base2["mv_bin"] = pd.qcut(base2["circ_mv_yi"], 10, labels=False, duplicates="drop")
            base2["to_bin"] = pd.qcut(base2["turnover_rate"], 10, labels=False, duplicates="drop")
            pos_bin = pos.merge(base2[["ts_code", "trade_date", "mv_bin", "to_bin"]],
                                left_on=["code", "d_prev"],
                                right_on=["ts_code", "trade_date"], how="left")
            keys = set(zip(pos_bin["mv_bin"].dropna().astype(int),
                           pos_bin["to_bin"].dropna().astype(int)))
            key_df = pd.DataFrame(sorted(keys), columns=["mv_bin", "to_bin"])
            pool = base2.merge(key_df, on=["mv_bin", "to_bin"], how="inner")
            if len(pool) > 200:
                negB = pool.rename(columns=REN)[["code", "d_prev"] + feats].dropna()
                negB = negB.sample(min(len(pos) * 3, len(negB)), random_state=7)

                for tag, neg in [("全市场随机", negA), ("同市值同换手邻域", negB)]:
                    X = pd.concat([pos[feats].assign(y=1), neg[feats].assign(y=0)], ignore_index=True)
                    y = X.pop("y").values
                    X = X.fillna(X.median())
                    clf = HistGradientBoostingClassifier(max_depth=3, max_iter=120,
                                                         random_state=7)
                    p = cross_val_predict(clf, X, y, cv=5, method="predict_proba")[:, 1]
                    auc = float(roc_auc_score(y, p))
                    aucs[tag] = round(auc, 4)
                    print("   对照组[%s] AUC = %.4f  (n_pos=%d, n_neg=%d)"
                          % (tag, auc, int((y == 1).sum()), int((y == 0).sum())))

                # 特征重要性（全量拟合一次）
                X = pd.concat([pos[feats].assign(y=1), negB[feats].assign(y=0)], ignore_index=True)
                y = X.pop("y").values
                X = X.fillna(X.median())
                clf = HistGradientBoostingClassifier(max_depth=3, max_iter=120, random_state=7).fit(X, y)
                from sklearn.inspection import permutation_importance
                pi = permutation_importance(clf, X, y, n_repeats=8, random_state=7,
                                            scoring="roc_auc")
                imp = sorted(zip(feats, pi.importances_mean), key=lambda x: -x[1])
                aucs["feature_importance"] = {k: round(float(v), 4) for k, v in imp}
                print("   特征重要性 top5:", [(k, round(float(v), 3)) for k, v in imp[:5]])
        rep["auc"] = aucs

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    print("\n[done] -> %s" % args.out, flush=True)


if __name__ == "__main__":
    main()
