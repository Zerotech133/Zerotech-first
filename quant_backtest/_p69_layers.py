# -*- coding: utf-8 -*-
"""P69 补充：质量因子的分层单调性 + IS/OOS 稳定性。

IC≈0 只能说明「没有整体单调关系」，不能说明两端如何。本脚本把每因子按
组内分位切 5 档，看各档平均未来收益是否单调，以判断：
  · 单调递增 → 质量溢价存在（只是弱）
  · 单调递减 → 质量因子在 A 股反向（垃圾股效应）
  · 无规律   → 真的无关
"""
import json
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, r"C:/项目/老田的AI助手/quant_backtest")
import _p69_fin_ic as M

OUT = r"C:/项目/老田的AI助手/quant_backtest/reports/_p69_layers.json"


def main():
    o, c, p, v = M.load_px()
    fin = M.load_fin()
    all_d = [int(d) for d in c.index if M.START <= int(d) <= M.END]
    dates = all_d[::M.STEP]
    fin_set = set(fin["full"].tolist())
    codes = [x for x in c.columns if x in fin_set]

    pan = M.build_panel(fin, dates, codes)
    per = pan.pop("period")
    nav = (1 + p.fillna(0) / 100.0).cumprod()
    adj = nav / c
    fwd = (c * adj).shift(-M.HOLD) / (o * adj).shift(-1) - 1.0
    trd = ((v > 0).fillna(False).astype(bool)).reindex(
        index=dates, columns=codes, fill_value=False)
    fw = fwd.reindex(index=dates, columns=codes)

    per_l = per.stack().rename("rp")
    per_l = per_l[per_l.notna()]
    base = pd.concat([per_l, fw.stack().rename("y"), trd.stack().rename("trd")], axis=1)
    base = base[base["y"].notna() & base["trd"].fillna(False).astype(bool)]

    xcols = {f: M.winsorize(pan[f]).stack().rename(f) for f in M.FACTORS}
    X = pd.concat(xcols.values(), axis=1)
    d = base.join(X, how="inner").reset_index()
    d.columns = ["date", "code", "rp", "y", "trd"] + M.FACTORS
    d = d.drop(columns=["trd"])

    g = d.groupby(["date", "rp"], sort=False)
    d["n_grp"] = g[M.FACTORS[0]].transform("size")
    d[M.FACTORS] = g[M.FACTORS].rank(pct=True)
    d = d[d["n_grp"] >= M.MIN_GROUP].copy()
    print("长表 %d 行，%d 天\n" % (len(d), d["date"].nunique()), flush=True)

    res = {}
    print("%-13s %10s %10s %10s %10s %10s  %s" % (
        "因子", "Q1(最低)", "Q2", "Q3", "Q4", "Q5(最高)", "形态"))
    for f in M.FACTORS:
        dd = d[["date", f, "y"]].dropna(subset=[f, "y"])
        # 每日按分位切 5 档，取每档等权平均收益，再按日等权（避免笔数加权偏差）
        dd = dd.assign(q=np.ceil(dd[f] * 5).clip(1, 5).astype(int))
        per_day = dd.groupby(["date", "q"])["y"].mean().unstack()
        if per_day.shape[1] < 5:
            res[f] = {"error": "档位不足"}
            continue
        qm = per_day.mean() * 100          # 每档的日均收益（%）
        # 单调性：Q1→Q5 的 Spearman
        rho = pd.Series(qm).corr(pd.Series(range(1, 6)), method="spearman")
        shape = ("单调递增" if rho > 0.8 else "单调递减" if rho < -0.8
                 else "无规律")
        res[f] = {
            "q_mean_pct": {int(k): round(float(x), 4) for k, x in qm.items()},
            "spearman": round(float(rho), 3),
            "shape": shape,
            "q5_minus_q1": round(float(qm[5] - qm[1]), 4),
        }
        print("%-13s %9.3f%% %9.3f%% %9.3f%% %9.3f%% %9.3f%%  %s (ρ=%.2f)" % (
            f, qm[1], qm[2], qm[3], qm[4], qm[5], shape, rho))

    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n-> %s" % OUT)


if __name__ == "__main__":
    main()
