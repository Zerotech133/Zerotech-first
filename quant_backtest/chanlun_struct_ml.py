# -*- coding: utf-8 -*-
"""结构特征 → 树模型：检验「缠论结构」有没有可用的横截面信息。

问题
----
v4 已判定六类信号作为独立入场依据全部无效（扣成本后净 alpha FM t 全 <1.96）。
但毛 alpha 的名义 t 高达 5.12，说明结构与未来收益**有相关性**，只是不足以
单独构成信号。那么它能不能作为**特征**，在信号内部做横截面排序？

这个实验最容易自欺，所以判据先立死：

  1. 时间切分（不是随机切分）：训练 ≤2025-06，测试 ≥2025-07
  2. 必须打赢两个基线，否则不算数：
     · base_all  —— 无脑做全部信号
     · base_type —— 只按信号类型的历史均值排序（模型若只是学会了
                    "1买优于3卖"，就等于 base_type，无增量）
  3. 主判据是同类型内部的分层单调性——模型能不能在 1买 里挑出更好的 1买
  4. 全部看净 alpha（standard 成本已扣）与 FM 月度聚类 t，不看名义 t

用法:
    python chanlun_struct_ml.py
    python chanlun_struct_ml.py --train-end 2025-06-30 --hold 10
    python chanlun_struct_ml.py --no-type        # 剔除 type，只看纯结构/量价
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

DATA = os.path.join(HERE, "data")
DS = os.path.join(DATA, "chanlun_struct_ds.parquet")
OUT = os.path.join(DATA, "chanlun_struct_ml.json")
TYPES = ["1买", "2买", "3买", "1卖", "2卖", "3卖"]
NOTIONAL = 1e5  # 单笔 10 万，与 v4 基准一致


# ── 成本（与 chanlun_cost_model 同口径，standard 档） ──────────
def _cost(px, adv, sigma, notional=NOTIONAL):
    if not px or not adv or px <= 0 or adv <= 0:
        return None
    comm_side = max(notional * 0.00025, 5.0)
    comm = 2.0 * comm_side / notional
    stamp = 0.0005
    transfer = 2.0 * 0.00001
    tick = 0.01 / px
    slip = 2.0 * max(0.0002, 0.5 * tick)
    impact = 2.0 * 0.6 * (sigma or 0.0) * math.sqrt(notional / adv)
    return comm + stamp + transfer + slip + impact


def _blk(v):
    v = [x for x in v if x is not None and not (isinstance(x, float) and math.isnan(x))]
    n = len(v)
    if n < 2:
        return None
    mu = float(np.mean(v))
    sd = float(np.std(v, ddof=1))
    return {"n": n, "avg": round(mu * 100, 3),
            "t": round(mu / (sd / math.sqrt(n)), 2) if sd > 0 else None,
            "win": round(sum(1 for x in v if x > 0) * 100.0 / n, 1)}


def _fm(df, cols_val="net_alpha"):
    """月度聚类 t：按入场月份聚合后再算。"""
    g = df.dropna(subset=[cols_val]).groupby(df["entry_date"].str[:7])[cols_val]
    if len(g) < 6:
        return None
    mo = g.mean().values
    mu = float(mo.mean())
    sd = float(mo.std(ddof=1))
    return {"months": len(mo), "avg": round(mu * 100, 3),
            "t": round(mu / (sd / math.sqrt(len(mo))), 2) if sd > 0 else None}


def _daily_ic(df, pred="pred", y="y_alpha", min_n=5):
    """按日 rank IC（Spearman）+ 按日聚类的 t。"""
    ics = []
    for _, g in df.groupby("entry_date"):
        if len(g) < min_n:
            continue
        if g[pred].nunique() < 2 or g[y].nunique() < 2:
            continue
        ics.append(g[pred].rank().corr(g[y].rank()))
    ics = [x for x in ics if x is not None and not math.isnan(x)]
    if len(ics) < 20:
        return None
    a = np.array(ics)
    mu, sd = float(a.mean()), float(a.std(ddof=1))
    return {"days": len(a), "ic": round(mu, 4),
            "t": round(mu / (sd / math.sqrt(len(a))), 2) if sd > 0 else None,
            "pos_rate": round((a > 0).mean() * 100, 1)}


def _qtable(df, q=5, pred="pred", label=""):
    """按预测值分组，看净 alpha 单调性。"""
    d = df.dropna(subset=[pred, "net_alpha"]).copy()
    if len(d) < q * 20:
        return {}
    d["grp"] = pd.qcut(d[pred].rank(method="first"), q, labels=False)
    out = {}
    for g, sub in d.groupby("grp"):
        out[int(g)] = {"n": len(sub),
                       "net_alpha": _blk(sub["net_alpha"].tolist()),
                       "raw": _blk(sub["y_raw"].tolist())}
    if label:
        print("  %s 分组（pred→净alpha）:" % label)
        for g in sorted(out):
            b = out[g]["net_alpha"]
            if b:
                print("    Q%d n=%5d 净alpha %+6.2f%% t=%5s win=%4.1f%%"
                      % (g + 1, b["n"], b["avg"], b["t"], b["win"]))
    return out


def main():
    ap = argparse.ArgumentParser(description="结构特征树模型")
    ap.add_argument("--ds", default=DS)
    ap.add_argument("--train-end", default="2025-06-30")
    ap.add_argument("--hold", type=int, default=10)
    ap.add_argument("--no-type", action="store_true", help="剔除信号类型特征")
    ap.add_argument("--depth", type=int, default=3)
    ap.add_argument("--iters", type=int, default=300)
    ap.add_argument("--lr", type=float, default=0.05)
    ap.add_argument("--min-leaf", type=int, default=100)
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()

    df = pd.read_parquet(a.ds)
    print("数据集 %d 行 × %d 列" % df.shape)
    df["type_id"] = pd.Categorical(df["type"], categories=TYPES).codes.astype(float)
    # 成本与净标签
    df["cost"] = [_cost(p, d, s) for p, d, s in zip(df["px"], df["adv"], df["sigma"])]
    df = df[df["cost"].notna()].copy()
    df["net_alpha"] = df["y_alpha"] - df["cost"]
    df = df.sort_values("entry_date").reset_index(drop=True)

    feat = [c for c in df.columns if c.startswith(("st_", "q_", "f_", "m_"))]
    if not a.no_type:
        feat.append("type_id")
    print("特征 %d 个：%s" % (len(feat), ", ".join(feat)))

    tr = df[df["entry_date"] <= a.train_end]
    te = df[df["entry_date"] > a.train_end]
    print("训练 %d 行 (%s~%s) / 测试 %d 行 (%s~%s)"
          % (len(tr), tr["entry_date"].min(), tr["entry_date"].max(),
             len(te), te["entry_date"].min(), te["entry_date"].max()))
    if len(te) < 200 or len(tr) < 2000:
        print("样本不足，退出")
        return

    from sklearn.ensemble import HistGradientBoostingRegressor

    cat_mask = [f == "type_id" for f in feat]
    model = HistGradientBoostingRegressor(
        max_depth=a.depth, max_iter=a.iters, learning_rate=a.lr,
        min_samples_leaf=a.min_leaf, l2_regularization=1.0,
        max_bins=128, early_stopping=False, random_state=42,
        categorical_features=cat_mask,
    )
    t0 = time.time()
    model.fit(tr[feat], tr["y_alpha"])
    print("训练完成 %.1fs" % (time.time() - t0))

    tr = tr.copy()
    te = te.copy()
    tr["pred"] = model.predict(tr[feat])
    te["pred"] = model.predict(te[feat])

    # ── 基线 ────────────────────────────────────────────────
    type_mean = tr.groupby("type")["y_alpha"].mean()
    te["pred_type"] = te["type"].map(type_mean).fillna(0.0)

    res = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "ds": os.path.basename(a.ds), "hold": a.hold,
           "train_end": a.train_end, "notional": NOTIONAL,
           "n_train": len(tr), "n_test": len(te),
           "n_feat": len(feat), "features": feat,
           "use_type": not a.no_type,
           "params": {"max_depth": a.depth, "max_iter": a.iters,
                      "lr": a.lr, "min_leaf": a.min_leaf,
                      "l2": 1.0}}

    print("\n" + "=" * 72)
    print("【测试集】全局对照（净 alpha 已扣 standard 10万 成本）")
    print("=" * 72)
    print("%-18s %6s %10s %8s %8s %8s" % ("口径", "n", "净alpha%", "t", "FM_t", "win%"))
    base = {}
    for name, col in [("base_all", None), ("base_type", "pred_type"),
                      ("model", "pred")]:
        if col is None:
            b = _blk(te["net_alpha"].tolist())
            fm = _fm(te)
        else:
            top = te[te[col] >= te[col].quantile(0.8)]
            b = _blk(top["net_alpha"].tolist())
            fm = _fm(top)
        base[name] = {"all_net": b, "top20": b if col else None, "fm": fm}
        print("%-18s %6d %+10.2f %8s %8s %8.1f"
              % (name, b["n"] if b else 0, b["avg"] if b else 0,
                 b["t"] if b else "-", (fm or {}).get("t", "-"),
                 b["win"] if b else 0))
    res["baselines"] = base

    print("\n训练集 vs 测试集 IC（按日 rank IC）：")
    tr_ic, te_ic = _daily_ic(tr), _daily_ic(te)
    print("  训练 IC %s t=%s (%s 天) / 测试 IC %s t=%s (%s 天)"
          % ((tr_ic or {}).get("ic"), (tr_ic or {}).get("t"), (tr_ic or {}).get("days"),
             (te_ic or {}).get("ic"), (te_ic or {}).get("t"), (te_ic or {}).get("days")))
    res["ic_train"] = tr_ic
    res["ic_test"] = te_ic

    print()
    res["q_model"] = _qtable(te, 5, "pred", "测试集 模型")
    print()
    res["q_type"] = _qtable(te, 5, "pred_type", "测试集 base_type")

    # ── 同类型内部分层（核心判据） ────────────────────────────
    print("\n" + "=" * 72)
    print("【核心判据】同类型内部：模型能否在同类信号里挑出更好的")
    print("=" * 72)
    by_type = {}
    for t in TYPES:
        sub = te[te["type"] == t].dropna(subset=["pred", "net_alpha"])
        if len(sub) < 100:
            continue
        hi = sub[sub["pred"] >= sub["pred"].quantile(0.8)]
        lo = sub[sub["pred"] <= sub["pred"].quantile(0.2)]
        ic = _daily_ic(sub)
        bh, bl = _blk(hi["net_alpha"].tolist()), _blk(lo["net_alpha"].tolist())
        ba = _blk(sub["net_alpha"].tolist())
        fh = _fm(hi)
        by_type[t] = {"n": len(sub), "all": ba, "top20": bh, "bot20": bl,
                      "spread": round((bh["avg"] - bl["avg"]), 3) if bh and bl else None,
                      "ic": ic, "fm_top": fh}
        print("  %s n=%5d  全体 %+6.2f%%  模型TOP20 %+6.2f%%(FM_t %s)  "
              "BOT20 %+6.2f%%  价差 %+6.2f%%  IC %s(t=%s)"
              % (t, len(sub), ba["avg"] if ba else 0,
                 bh["avg"] if bh else 0, (fh or {}).get("t", "-"),
                 bl["avg"] if bl else 0,
                 by_type[t]["spread"] or 0,
                 (ic or {}).get("ic"), (ic or {}).get("t")))
    res["by_type"] = by_type

    # ── 特征重要性（permutation，测试集） ─────────────────────
    from sklearn.inspection import permutation_importance

    print("\n特征重要性（permutation，测试集，净 IC 下降）：")
    sub = te.sample(min(len(te), 4000), random_state=42)
    Xs, ys = sub[feat], sub["y_alpha"]
    try:
        pi = permutation_importance(model, Xs, ys, n_repeats=5,
                                    random_state=42, scoring="r2")
        order = np.argsort(-pi.importances_mean)
        imp = {}
        for i in order[:12]:
            imp[feat[i]] = round(float(pi.importances_mean[i]), 5)
            print("  %-18s %+.5f" % (feat[i], pi.importances_mean[i]))
        res["importance"] = imp
    except Exception as e:
        print("  跳过：%s" % e)

    # ── 分年稳定性 ──────────────────────────────────────────
    print("\n测试集分年（模型 TOP20 vs 全体）：")
    yy = {}
    for y in sorted(te["entry_date"].str[:4].unique()):
        s = te[te["entry_date"].str[:4] == y]
        top = s[s["pred"] >= s["pred"].quantile(0.8)]
        b, bt = _blk(s["net_alpha"].tolist()), _blk(top["net_alpha"].tolist())
        yy[y] = {"all": b, "top20": bt}
        print("  %s 全体 %+6.2f%% (n=%d)  TOP20 %+6.2f%% (n=%d)"
              % (y, b["avg"] if b else 0, b["n"] if b else 0,
                 bt["avg"] if bt else 0, bt["n"] if bt else 0))
    res["by_year"] = yy

    # ── 判定 ────────────────────────────────────────────────
    print("\n" + "=" * 72)
    verdict = []
    m_top = base["model"]
    bt_top = base["base_type"]
    ic = te_ic or {}
    if ic.get("t") is not None and ic["t"] > 1.96:
        verdict.append("IC 通过（t=%.2f > 1.96）" % ic["t"])
    else:
        verdict.append("IC 未通过（t=%s ≤ 1.96）" % ic.get("t"))
    if (m_top.get("fm") or {}).get("t") is not None and m_top["fm"]["t"] > 1.96:
        verdict.append("模型 TOP20 的 FM t=%.2f 通过" % m_top["fm"]["t"])
    else:
        verdict.append("模型 TOP20 的 FM t=%s 未通过" % (m_top.get("fm") or {}).get("t"))
    if m_top["all_net"]["avg"] > bt_top["all_net"]["avg"]:
        verdict.append("模型优于 base_type（%+.2f vs %+.2f）"
                       % (m_top["all_net"]["avg"], bt_top["all_net"]["avg"]))
    else:
        verdict.append("**模型未跑赢 base_type（%+.2f vs %+.2f）**"
                       % (m_top["all_net"]["avg"], bt_top["all_net"]["avg"]))
    for v in verdict:
        print("  · " + v)
    res["verdict"] = verdict

    with open(a.out + ".tmp", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    os.replace(a.out + ".tmp", a.out)
    print("\n-> %s" % a.out)


if __name__ == "__main__":
    main()
