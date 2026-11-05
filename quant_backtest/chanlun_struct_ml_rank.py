# -*- coding: utf-8 -*-
"""改用日内横截面分位当标签：剔除 regime 漂移后，结构还有排序力吗？

诊断结论是「信息存在（随机切分 IC 0.107）但不可跨期外推」，根因是标签在
时间上剧烈漂移：净 alpha 2024H1 +2.88% → 2025H1 -3.18%，跨度 6 个百分点。
用绝对 alpha 当标签，模型会去拟合「那个时期什么特征好」，换 regime 就废。

日内分位标签把这个问题从根上掐掉：每天只问「今天这批信号里谁更好」，
目标分布每天都是均匀的，regime 的绝对水平被消掉。

注意：这只是**排序能力**的检验，不等于能赚钱——池子整体扣成本后是负期望
（-0.45%），排序最好也只能做到少亏。所以判据是「TOP 组相对全体的超额」，
而不是「TOP 组绝对为正」。

用法: python chanlun_struct_ml_rank.py
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from chanlun_struct_ml import _blk, _cost, _daily_ic, _fm  # noqa: E402

DATA = os.path.join(HERE, "data")
DS = os.path.join(DATA, "chanlun_struct_ds.parquet")
OUT = os.path.join(DATA, "chanlun_struct_rank.json")
TYPES = ["1买", "2买", "3买", "1卖", "2卖", "3卖"]


def _fit(tr, te, feat, ycol, depth=3, iters=300, leaf=100):
    from sklearn.ensemble import HistGradientBoostingRegressor

    m = HistGradientBoostingRegressor(
        max_depth=depth, max_iter=iters, learning_rate=0.05,
        min_samples_leaf=leaf, l2_regularization=1.0, max_bins=128,
        early_stopping=False, random_state=42,
        categorical_features=[f == "type_id" for f in feat])
    m.fit(tr[feat], tr[ycol])
    return m, m.predict(te[feat])


def main():
    df = pd.read_parquet(DS)
    df["type_id"] = pd.Categorical(df["type"], categories=TYPES).codes.astype(float)
    df["cost"] = [_cost(p, d, s) for p, d, s in zip(df["px"], df["adv"], df["sigma"])]
    df = df[df["cost"].notna()].copy()
    df["net_alpha"] = df["y_alpha"] - df["cost"]
    df = df.sort_values(["entry_date", "code"]).reset_index(drop=True)

    # 日内横截面分位（0~1）；当天不足 5 个信号的样本剔除（分位无意义）
    cnt = df.groupby("entry_date")["code"].transform("count")
    df = df[cnt >= 5].copy()
    df["y_rank"] = df.groupby("entry_date")["y_alpha"].rank(pct=True)
    print("数据集 %d 行，日内分位标签；日均信号 %.1f 个"
          % (len(df), len(df) / df["entry_date"].nunique()))

    feat = [c for c in df.columns if c.startswith(("st_", "q_", "f_", "m_"))] + ["type_id"]
    st = [c for c in feat if c.startswith("st_")]
    q = [c for c in feat if c.startswith("q_")]

    cut = "2025-06-30"
    tr, te = df[df["entry_date"] <= cut], df[df["entry_date"] > cut]
    print("训练 %d / 测试 %d" % (len(tr), len(te)))
    out = {"n": len(df), "cut": cut, "n_train": len(tr), "n_test": len(te)}

    print("\n【1】分位标签的 IC（测试集，按日 rank IC）")
    print("  %-16s %2s %10s %8s %10s %8s"
          % ("特征集", "n", "训练IC", "训练t", "测试IC", "测试t"))
    res_ic = {}
    preds = {}
    for name, f in [("全量", feat), ("仅结构", st), ("仅量价", q)]:
        m, p = _fit(tr, te, f, "y_rank")
        trp = tr.assign(pred=m.predict(tr[f]))
        tep = te.assign(pred=p)
        a, b = _daily_ic(trp), _daily_ic(tep)
        res_ic[name] = {"train": a, "test": b, "n_feat": len(f)}
        preds[name] = p
        print("  %-16s %2d %10.4f %8s %10.4f %8s"
              % (name, len(f), a["ic"], a["t"], b["ic"], b["t"]))
    out["ic"] = res_ic

    # ── 2. 排序能否换钱：TOP 组的真实净 alpha ────────────────
    print("\n【2】排序到钱的转换（净 alpha，standard 10万 已扣）")
    print("  %-14s %7s %10s %8s %8s %8s" % ("口径", "n", "净alpha%", "t", "FM_t", "相对全体"))
    allblk = _blk(te["net_alpha"].tolist())
    print("  %-14s %7d %+10.2f %8s %8s %8s"
          % ("全体", allblk["n"], allblk["avg"], allblk["t"], "-", "-"))
    out["all"] = allblk
    top_res = {}
    for name, p in preds.items():
        tep = te.assign(pred=p)
        for qq, lbl in [(0.8, "TOP20"), (0.9, "TOP10")]:
            sel = tep[tep["pred"] >= tep["pred"].quantile(qq)]
            b = _blk(sel["net_alpha"].tolist())
            fm = _fm(sel)
            d = round(b["avg"] - allblk["avg"], 3)
            top_res["%s_%s" % (name, lbl)] = {"blk": b, "fm": fm, "vs_all": d}
            print("  %-14s %7d %+10.2f %8s %8s %+8.2f"
                  % ("%s %s" % (name, lbl), b["n"], b["avg"], b["t"],
                     (fm or {}).get("t", "-"), d))
    out["top"] = top_res

    # ── 3. 分年稳定性 ───────────────────────────────────────
    print("\n【3】全量模型 TOP20 分年")
    tep = te.assign(pred=preds["全量"])
    yy = {}
    for y in sorted(tep["entry_date"].str[:4].unique()):
        s = tep[tep["entry_date"].str[:4] == y]
        top = s[s["pred"] >= s["pred"].quantile(0.8)]
        a, b = _blk(s["net_alpha"].tolist()), _blk(top["net_alpha"].tolist())
        yy[y] = {"all": a, "top20": b}
        print("  %s 全体 %+6.2f%% (n=%4d)  TOP20 %+6.2f%% (n=%4d)  超额 %+5.2fpp"
              % (y, a["avg"], a["n"], b["avg"], b["n"], b["avg"] - a["avg"]))
    out["by_year"] = yy

    # ── 4. 同类型内部（核心） ───────────────────────────────
    print("\n【4】同类型内部 TOP20 vs BOT20")
    bt = {}
    for t in TYPES:
        s = tep[tep["type"] == t]
        if len(s) < 150:
            continue
        hi = s[s["pred"] >= s["pred"].quantile(0.8)]
        lo = s[s["pred"] <= s["pred"].quantile(0.2)]
        bh, bl = _blk(hi["net_alpha"].tolist()), _blk(lo["net_alpha"].tolist())
        ba = _blk(s["net_alpha"].tolist())
        bt[t] = {"n": len(s), "all": ba, "top": bh, "bot": bl}
        print("  %s n=%5d 全体 %+6.2f%%  TOP %+6.2f%%  BOT %+6.2f%%  价差 %+6.2fpp"
              % (t, len(s), ba["avg"], bh["avg"], bl["avg"], bh["avg"] - bl["avg"]))
    out["by_type"] = bt

    with open(OUT + ".tmp", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    os.replace(OUT + ".tmp", OUT)
    print("\n-> %s" % OUT)


if __name__ == "__main__":
    main()
