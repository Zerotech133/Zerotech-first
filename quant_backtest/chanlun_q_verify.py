# -*- coding: utf-8 -*-
"""q_ 子集（量价特征）的 IC 0.03 是真的，还是风格暴露 + 多重比较？

诊断一发现「仅量价 q_」是唯一能通过时间切分的特征子集（测试 IC +0.0302,
t=3.15），但两个疑点必须先排除：

  疑点 1 多重比较 —— 我试了 5 个特征子集，挑最好的那个报 t。需要换切分点
         看是否稳定；若不稳，就是选择偏差。
  疑点 2 风格暴露 —— q_logpx / q_logadv / q_vol20 本质是市值与波动因子。
         若 top20 全是小盘低波，那 IC 来自风格 beta，不是选股信息，且这种
         暴露在风格反转时会一起崩。

判据：
  · 多个切分点（2024-12 / 2025-06 / 2025-12 / 2026-03）测试 IC 同号 → 稳
  · top20 与 bot20 的市值/波动差异被中性化后 IC 仍显著 → 不是纯风格

用法: python chanlun_q_verify.py
"""

from __future__ import annotations

import json
import math
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
OUT = os.path.join(DATA, "chanlun_q_verify.json")
TYPES = ["1买", "2买", "3买", "1卖", "2卖", "3卖"]
CUTS = ["2024-12-31", "2025-06-30", "2025-12-31", "2026-03-31"]


def _prep():
    df = pd.read_parquet(DS)
    df["type_id"] = pd.Categorical(df["type"], categories=TYPES).codes.astype(float)
    df["cost"] = [_cost(p, d, s) for p, d, s in zip(df["px"], df["adv"], df["sigma"])]
    df = df[df["cost"].notna()].copy()
    df["net_alpha"] = df["y_alpha"] - df["cost"]
    return df.sort_values("entry_date").reset_index(drop=True)


def _fit(tr, te, feat, depth=3, iters=300, leaf=100):
    from sklearn.ensemble import HistGradientBoostingRegressor

    m = HistGradientBoostingRegressor(
        max_depth=depth, max_iter=iters, learning_rate=0.05,
        min_samples_leaf=leaf, l2_regularization=1.0, max_bins=128,
        early_stopping=False, random_state=42,
        categorical_features=[f == "type_id" for f in feat])
    m.fit(tr[feat], tr["y_alpha"])
    return m, m.predict(te[feat])


def main():
    df = _prep()
    q = [c for c in df.columns if c.startswith("q_")]
    st = [c for c in df.columns if c.startswith("st_")]
    allf = q + st + ["type_id"]
    out = {"n": len(df), "q_feats": q, "cuts": CUTS}

    # ── 1. 多切分点稳定性 ────────────────────────────────────
    print("【1】多切分点稳定性（q_ 子集，时间前向切分）")
    print("  %-12s %7s %7s %10s %9s %10s %9s"
          % ("切点", "n_tr", "n_te", "训练IC", "训练t", "测试IC", "测试t"))
    rows = []
    for c in CUTS:
        tr, te = df[df["entry_date"] <= c], df[df["entry_date"] > c]
        if len(te) < 1000:
            continue
        m, p = _fit(tr, te, q)
        tr2 = tr.assign(pred=m.predict(tr[q]))
        te2 = te.assign(pred=p)
        ia, ib = _daily_ic(tr2), _daily_ic(te2)
        rows.append({"cut": c, "n_tr": len(tr), "n_te": len(te),
                     "train": ia, "test": ib})
        print("  %-12s %7d %7d %10.4f %9s %10.4f %9s"
              % (c, len(tr), len(te), ia["ic"], ia["t"], ib["ic"], ib["t"]))
    out["cuts_detail"] = rows
    ics = [r["test"]["ic"] for r in rows]
    ts = [r["test"]["t"] for r in rows]
    pos = sum(1 for x in ics if x > 0)
    print("  ⇒ 测试 IC 同号 %d/%d，t 值 %s" % (pos, len(ics), ts))
    out["cut_positive"] = "%d/%d" % (pos, len(ics))

    # ── 2. 风格暴露检验 ─────────────────────────────────────
    print("\n【2】风格暴露：q_ 模型的 TOP20 vs BOT20 是什么股票")
    cut = "2025-06-30"
    tr, te = df[df["entry_date"] <= cut], df[df["entry_date"] > cut]
    m, p = _fit(tr, te, q)
    te = te.assign(pred=p)
    hi = te[te["pred"] >= te["pred"].quantile(0.8)]
    lo = te[te["pred"] <= te["pred"].quantile(0.2)]
    print("  %-14s %12s %12s %10s" % ("特征", "TOP20均值", "BOT20均值", "差异σ"))
    style = {}
    for f in ["q_logpx", "q_logadv", "q_vol20", "q_vol60", "q_illiq",
              "q_pre20", "q_volratio", "q_pos20"]:
        if f not in te.columns:
            continue
        a, b = hi[f].mean(), lo[f].mean()
        sd = te[f].std()
        style[f] = {"top": round(float(a), 4), "bot": round(float(b), 4),
                    "diff_sd": round(float((a - b) / sd), 2) if sd else None}
        print("  %-14s %12.4f %12.4f %10.2f"
              % (f, a, b, (a - b) / sd if sd else 0))
    out["style"] = style

    # ── 3. 风格中性化后剩余 IC ───────────────────────────────
    print("\n【3】把市值/波动/流动性从中性化，看 q_ 还剩多少 IC")
    # 每日横截面回归掉 log市值(logadv 代理)、vol20、illiq，取残差为目标
    d = df.copy()
    d["ym"] = d["entry_date"]
    resid = []
    for _, g in d.groupby("ym"):
        if len(g) < 10:
            resid.append(pd.Series(np.nan, index=g.index))
            continue
        X = g[["q_logadv", "q_vol20", "q_illiq"]].astype(float)
        X = X.fillna(X.median()).fillna(0.0)
        X = (X - X.mean()) / X.std().replace(0, 1)
        X.insert(0, "_c", 1.0)
        y = g["y_alpha"].astype(float).values
        try:
            beta, *_ = np.linalg.lstsq(X.values, y, rcond=None)
            resid.append(pd.Series(y - X.values @ beta, index=g.index))
        except Exception:
            resid.append(pd.Series(np.nan, index=g.index))
    d["y_resid"] = pd.concat(resid).sort_index()
    d = d.dropna(subset=["y_resid"])

    tr2, te2 = d[d["entry_date"] <= cut], d[d["entry_date"] > cut]
    m2, p2 = _fit(tr2, te2, q)
    # 训练用原始 alpha，评估用残差 alpha（检验模型预测是否含风格外信息）
    ic_orig = _daily_ic(te2.assign(pred=p2), y="y_alpha")
    ic_resid = _daily_ic(te2.assign(pred=p2), y="y_resid")
    print("  原始 alpha 上 IC %.4f (t=%s) / 风格中性化后 IC %.4f (t=%s)"
          % (ic_orig["ic"], ic_orig["t"], ic_resid["ic"], ic_resid["t"]))
    out["neutral_ic"] = {"orig": ic_orig, "resid": ic_resid}

    # ── 4. 净收益：TOP20 到底赚不赚钱 ─────────────────────────
    print("\n【4】q_ 模型 TOP20 的净收益（standard 10万 成本已扣）")
    for cut in ["2025-06-30"]:
        tr, te = df[df["entry_date"] <= cut], df[df["entry_date"] > cut]
        m, p = _fit(tr, te, q)
        te = te.assign(pred=p)
        for qq, lbl in [(0.8, "TOP20"), (0.5, "TOP50以上")]:
            sel = te[te["pred"] >= te["pred"].quantile(qq)]
            b = _blk(sel["net_alpha"].tolist())
            fm = _fm(sel)
            print("  %-10s n=%5d 净alpha %+6.2f%% t=%5s FM_t=%5s win=%.1f%%"
                  % (lbl, b["n"], b["avg"], b["t"], (fm or {}).get("t"), b["win"]))
            out.setdefault("net_top", {})[lbl] = {"blk": b, "fm": fm}
        ball = _blk(te["net_alpha"].tolist())
        print("  %-10s n=%5d 净alpha %+6.2f%% t=%5s"
              % ("全体", ball["n"], ball["avg"], ball["t"]))
        out["net_all"] = ball

    # ── 5. 分年 ─────────────────────────────────────────────
    print("\n【5】分年（q_ 模型 TOP20）")
    tr, te = df[df["entry_date"] <= "2025-06-30"], df[df["entry_date"] > "2025-06-30"]
    m, p = _fit(tr, te, q)
    te = te.assign(pred=p)
    yy = {}
    for y in sorted(te["entry_date"].str[:4].unique()):
        s = te[te["entry_date"].str[:4] == y]
        top = s[s["pred"] >= s["pred"].quantile(0.8)]
        a, b = _blk(s["net_alpha"].tolist()), _blk(top["net_alpha"].tolist())
        yy[y] = {"all": a, "top20": b}
        print("  %s 全体 %+6.2f%% (n=%4d)  TOP20 %+6.2f%% (n=%4d)"
              % (y, a["avg"], a["n"], b["avg"], b["n"]))
    out["by_year"] = yy

    with open(OUT + ".tmp", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    os.replace(OUT + ".tmp", OUT)
    print("\n-> %s" % OUT)


if __name__ == "__main__":
    main()
