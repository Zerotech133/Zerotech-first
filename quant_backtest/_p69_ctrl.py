# -*- coding: utf-8 -*-
"""P69 对照实验：用同一套截面流程跑 P67 已知有效的量价因子。

目的：证明「质量因子 IC≈0」不是流程算错，而是因子真的无效。
P67 独立实现给出 maxret20 IC = -0.067（t=-5.42）、turn20 -0.041。
若本流程能复现相近量级与符号，则 P69 的口径可信。

差异点：量价因子与报告期无关，不做「按报告期分组排名」（那会人为打散真实信号），
只做普通日截面排名。其余（快照采样、fwd、tradable、月度聚合）完全一致。
"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, r"C:/项目/老田的AI助手/quant_backtest")
import _p69_fin_ic as M

OUT = r"C:/项目/老田的AI助手/quant_backtest/reports/_p69_ctrl.json"

# P69_WIN=p67 时切到 P67 的原窗口与日频，用于逐项对齐验证
if os.environ.get("P69_WIN") == "p67":
    M.START, M.END, M.STEP = 20251105, 20261105, 1
    OUT = r"C:/项目/老田的AI助手/quant_backtest/reports/_p69_ctrl_p67win.json"
    print(">>> 使用 P67 原窗口 %d-%d, STEP=1" % (M.START, M.END), flush=True)


def main():
    o, c, p, v = M.load_px()
    nav = (1 + p.fillna(0) / 100.0).cumprod()
    adj = nav / c
    c_adj = c * adj
    o_adj = o * adj
    fwd = c_adj.shift(-M.HOLD) / o_adj.shift(-1) - 1.0
    tradable = (v > 0).fillna(False).astype(bool)

    # 复权后算量价因子（与 P67 同式）
    r1 = (p.fillna(0) / 100.0).where(v > 0)
    ctrl = {
        "maxret20": r1.rolling(20).max(),                    # P67: -0.067
        "vol20": r1.rolling(20).std(),                       # P67: -0.068
        "turn20": v / v.rolling(60).mean(),                  # P67: -0.041
        "mom20": c_adj / c_adj.shift(20) - 1,                # P67: -0.036
    }

    all_d = [int(d) for d in c.index if M.START <= int(d) <= M.END]
    dates = all_d[::M.STEP]
    codes = list(c.columns)
    print("dates %d, codes %d" % (len(dates), len(codes)), flush=True)

    fwd_w = fwd.reindex(index=dates, columns=codes)
    trd_w = tradable.reindex(index=dates, columns=codes, fill_value=False)
    y_l = fwd_w.stack().rename("y")
    tr_l = trd_w.stack().rename("trd")
    base = pd.concat([y_l, tr_l], axis=1)
    base = base[base["y"].notna() & base["trd"].fillna(False).astype(bool)]
    print("base 长表 %d 行" % len(base), flush=True)

    res = {}
    for f, df in ctrl.items():
        x_l = df.reindex(index=dates, columns=codes).stack().rename("xf")
        d = base.join(x_l, how="inner")
        d = d[d["xf"].notna()].reset_index()
        d.columns = ["date", "code", "y", "trd", "xf"]
        d["rk"] = d.groupby("date", sort=False)["xf"].rank(pct=True)
        agg = d.groupby("date").apply(M._day_stats, include_groups=False).dropna(subset=["ic"])
        if len(agg) < 20:
            res[f] = {"error": "样本不足", "n_days": int(len(agg))}
            continue
        arr = agg["ic"].to_numpy(dtype=float)
        s = pd.Series(arr, index=pd.Index([int(x) for x in agg.index]))
        mser = s.groupby([str(i)[:6] for i in s.index]).mean()
        mmu, msd = float(mser.mean()), float(mser.std(ddof=1))
        sp = agg["sp"].dropna()
        res[f] = {
            "ic_mean": round(float(arr.mean()), 4),
            "t_daily": round(float(arr.mean() / arr.std(ddof=1) * np.sqrt(len(arr))), 2),
            "n_days": int(len(arr)),
            "ic_m_mean": round(mmu, 4),
            "t_monthly": round(mmu / msd * np.sqrt(len(mser)), 2) if msd > 0 else None,
            "n_months": int(len(mser)),
            "pos_rate_m": round(float((mser > 0).mean()), 3),
            "ls_spread": round(float(sp.mean()), 6) if len(sp) else None,
        }

    print()
    print("%-11s %15s %9s %9s %9s %10s" % (
        "对照因子", "P67参考IC", "IC日频", "t日频", "IC月度", "多空spread"))
    ref = {"maxret20": -0.0668, "vol20": -0.0681, "turn20": -0.0407, "mom20": -0.0358}
    for k, r0 in res.items():
        if "error" in r0:
            print("%-11s %s" % (k, r0["error"]))
            continue
        print("%-11s %15.4f %9.4f %9.2f %9.4f %9s" % (
            k, ref[k], r0["ic_mean"], r0["t_daily"], r0["ic_m_mean"],
            "—" if r0["ls_spread"] is None else "%.3f%%" % (r0["ls_spread"] * 100)))

    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print("\n-> %s" % OUT)


if __name__ == "__main__":
    main()
