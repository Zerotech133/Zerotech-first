# -*- coding: utf-8 -*-
"""结构模型诊断：训练 IC 0.24 → 测试 IC 0.00，到底是过拟合还是概念漂移？

三种可能，处置方式完全不同：
  A. 记忆噪声   —— 随机切分也崩 → 模型没学到可泛化的东西，特征无信息
  B. 概念漂移   —— 随机切分好、时间切分崩 → 信息存在但不可外推（regime 依赖）
  C. 时间代理   —— m_cnt 一类"当日信号数"实际是市场状态的时间戳，
                   模型在背行情日期而不是学结构

本脚本回答：随机切分 vs 时间切分的 IC 落差、逐特征剔除、逐年 alpha 分布。

用法: python chanlun_struct_ml_diag.py
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

from chanlun_struct_ml import _cost, _daily_ic  # noqa: E402

DATA = os.path.join(HERE, "data")
DS = os.path.join(DATA, "chanlun_struct_ds.parquet")
OUT = os.path.join(DATA, "chanlun_struct_diag.json")
TYPES = ["1买", "2买", "3买", "1卖", "2卖", "3卖"]


def _prep(ds=DS):
    df = pd.read_parquet(ds)
    df["type_id"] = pd.Categorical(df["type"], categories=TYPES).codes.astype(float)
    df["cost"] = [_cost(p, d, s) for p, d, s in zip(df["px"], df["adv"], df["sigma"])]
    df = df[df["cost"].notna()].copy()
    df["net_alpha"] = df["y_alpha"] - df["cost"]
    return df.sort_values("entry_date").reset_index(drop=True)


def _fit_pred(tr, te, feat, depth=3, iters=300, lr=0.05, leaf=100):
    from sklearn.ensemble import HistGradientBoostingRegressor

    cat = [f == "type_id" for f in feat]
    m = HistGradientBoostingRegressor(
        max_depth=depth, max_iter=iters, learning_rate=lr,
        min_samples_leaf=leaf, l2_regularization=1.0, max_bins=128,
        early_stopping=False, random_state=42, categorical_features=cat)
    m.fit(tr[feat], tr["y_alpha"])
    return m.predict(tr[feat]), m.predict(te[feat]), m


def main():
    df = _prep()
    feat_all = [c for c in df.columns if c.startswith(("st_", "q_", "f_", "m_"))] + ["type_id"]
    print("数据集 %d 行，%d 特征" % (len(df), len(feat_all)))
    out = {"n": len(df)}

    # ── 1. 逐年/逐半年 alpha 分布：是否 regime shift ─────────────
    print("\n【1】标签随时间的漂移（净 alpha，standard 成本已扣）")
    print("  %-10s %7s %9s %9s %9s %8s"
          % ("期间", "n", "净alpha%", "raw%", "成本%", "win%"))
    per = {}
    df["half"] = df["entry_date"].str[:4] + "H" + np.where(
        df["entry_date"].str[5:7].astype(int) <= 6, "1", "2")
    for k, g in df.groupby("half"):
        na, yr, ct = g["net_alpha"], g["y_raw"], g["cost"]
        per[k] = {"n": len(g), "net": round(na.mean() * 100, 3),
                  "raw": round(yr.mean() * 100, 3),
                  "cost": round(ct.mean() * 100, 3),
                  "win": round((na > 0).mean() * 100, 1)}
        print("  %-10s %7d %+9.2f %+9.2f %+9.2f %8.1f"
              % (k, len(g), na.mean() * 100, yr.mean() * 100, ct.mean() * 100,
                 (na > 0).mean() * 100))
    out["by_half"] = per

    # ── 2. 随机切分 vs 时间切分 ────────────────────────────────
    print("\n【2】随机切分（同分布）vs 时间切分（前向）")
    rng = np.random.default_rng(42)
    idx = rng.permutation(len(df))
    cut = int(len(df) * 0.7)
    rtr, rte = df.iloc[idx[:cut]], df.iloc[idx[cut:]]
    ptr, pte, _ = _fit_pred(rtr, rte, feat_all)
    rtr = rtr.assign(pred=ptr)
    rte = rte.assign(pred=pte)
    ric = _daily_ic(rte)
    tr_ic = _daily_ic(rtr)
    print("  随机切分：训练 IC %s (t=%s) / 测试 IC %s (t=%s)"
          % (tr_ic["ic"], tr_ic["t"], ric["ic"], ric["t"]))

    # 时间切分（与主脚本一致）
    tr = df[df["entry_date"] <= "2025-06-30"]
    te = df[df["entry_date"] > "2025-06-30"]
    qtr, qte, _ = _fit_pred(tr, te, feat_all)
    tr = tr.assign(pred=qtr)
    te = te.assign(pred=qte)
    tic_tr, tic_te = _daily_ic(tr), _daily_ic(te)
    print("  时间切分：训练 IC %s (t=%s) / 测试 IC %s (t=%s)"
          % (tic_tr["ic"], tic_tr["t"], tic_te["ic"], tic_te["t"]))
    out["random_split"] = {"train_ic": tr_ic, "test_ic": ric}
    out["time_split"] = {"train_ic": tic_tr, "test_ic": tic_te}

    # 判定
    if ric["t"] is not None and abs(ric["t"]) > 1.96:
        verdict = "随机切分通过 → 信息存在，问题在外推（概念漂移）"
    else:
        verdict = "随机切分也崩 → 模型在记忆噪声，特征无稳定信息"
    print("  ⇒ " + verdict)
    out["split_verdict"] = verdict

    # ── 3. 剔除时间代理特征 m_cnt ─────────────────────────────
    print("\n【3】剔除时间代理特征后")
    for drop in ([], ["m_cnt"], ["m_cnt", "q_vol20"], ["m_cnt", "q_logadv", "q_logpx"]):
        f = [x for x in feat_all if x not in drop]
        a, b, _ = _fit_pred(tr, te, f)
        ic_a, ic_b = _daily_ic(tr.assign(pred=a)), _daily_ic(te.assign(pred=b))
        print("  剔除 %-28s 训练IC %.4f(t=%5s)  测试IC %+.4f(t=%5s)"
              % (str(drop or "无"), ic_a["ic"], ic_a["t"], ic_b["ic"], ic_b["t"]))
        out.setdefault("drop_test", []).append(
            {"drop": drop, "train_ic": ic_a, "test_ic": ic_b})

    # ── 4. 模型复杂度扫描（是否会跟着复杂化而崩塌） ──────────────
    print("\n【4】模型复杂度 → 训练/测试 IC")
    print("  %-24s %10s %10s %8s" % ("配置", "训练IC", "测试IC", "落差"))
    sweep = []
    for depth, iters, leaf in [(2, 100, 200), (2, 300, 200), (3, 300, 100),
                               (3, 600, 50), (6, 600, 20)]:
        a, b, _ = _fit_pred(tr, te, feat_all, depth=depth, iters=iters, leaf=leaf)
        ia = _daily_ic(tr.assign(pred=a))
        ib = _daily_ic(te.assign(pred=b))
        gap = (ia["ic"] or 0) - (ib["ic"] or 0)
        sweep.append({"depth": depth, "iters": iters, "leaf": leaf,
                      "train": ia, "test": ib, "gap": round(gap, 4)})
        print("  depth=%d iter=%d leaf=%3d  %10.4f %10.4f %8.4f"
              % (depth, iters, leaf, ia["ic"], ib["ic"], gap))
    out["sweep"] = sweep

    # ── 5. 只用结构特征（不含量价、不含市场状态） ────────────────
    print("\n【5】特征子集对比（时间切分）")
    groups = {
        "仅结构 st_": [c for c in feat_all if c.startswith("st_")],
        "仅量价 q_": [c for c in feat_all if c.startswith("q_")],
        "结构+量价": [c for c in feat_all if c.startswith(("st_", "q_"))] + ["type_id"],
        "仅布尔 f_": [c for c in feat_all if c.startswith("f_")],
        "全量": feat_all,
    }
    sub = {}
    for name, f in groups.items():
        if len(f) < 2:
            continue
        a, b, _ = _fit_pred(tr, te, f)
        ia = _daily_ic(tr.assign(pred=a))
        ib = _daily_ic(te.assign(pred=b))
        sub[name] = {"n_feat": len(f), "train": ia, "test": ib}
        print("  %-12s %2d 特征  训练IC %.4f(t=%5s)  测试IC %+.4f(t=%5s)"
              % (name, len(f), ia["ic"], ia["t"], ib["ic"], ib["t"]))
    out["feature_subsets"] = sub

    with open(OUT + ".tmp", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    os.replace(OUT + ".tmp", OUT)
    print("\n-> %s" % OUT)


if __name__ == "__main__":
    main()
