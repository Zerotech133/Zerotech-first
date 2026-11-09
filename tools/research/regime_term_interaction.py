# -*- coding: utf-8 -*-
"""势 × 持有期的交互：为什么之前八个势判据「全部反向」（K-RES-20261108-INTERACT）。

矛盾在哪里
----------
`regime_robust.py` 测的是**未来 20 日**：强市 −1.38%（t=−2.72），弱市 +2.18%。
`hold_term_lab.py` 测的是**未来 3 日**：强市 +2.41%，弱市 +0.50%。

同一个"势"，方向完全相反。之前把它当成「判据写反了」，于是去反转判据、然后
被稳健性推翻（剔 2024-09 后 t=−16.14）。但这两种说法不必矛盾：**短期动量 +
长期反转**是市场最稳定的经验规律之一。若如此，则"势"根本不是「能不能做多」的
问题，而是「这笔仓位该持多久」的问题：

    顺短期势 → 只能做短（3~5 日）
    逆长期势 → 拿不住长（20 日）

这才是"择势"与"择时"的正确耦合：**势不是开关，是期限的选择器。**

本脚本要证的正是这个交互项
--------------------------
1. 势用**事前固定阈值**分档（不做事后三分位，避免把分位边界拟合进样本）。
2. 持有期扫 1~20 日，看「势 × h」的矩阵是否出现符号翻转。
3. 交互项必须过三道：逐月、剔 top5、按笔等权（防加权假象）。

时间尺度也报一档：h=1 是隔日，h=3 是缠论说的"第一段"，h=20 是产线现行值。

用法:
    python tools/research/regime_term_interaction.py
    python tools/research/regime_term_interaction.py --json
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys
from collections import defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

from portfolio_mtm import BUY_COST, SELL_COST, load_price_panel  # noqa: E402

SHADOW = os.path.join(ROOT, "quant_backtest", "data", "combo_shadow.jsonl")
TDX = os.path.join(ROOT, "quant_backtest", "data", "tdx")
IDX_DIR = os.path.join(TDX, "ts_index_daily")
OUT = os.path.join(ROOT, "portal", "regime_term_interaction.json")

IDX_CODE = "000985.CSI"
HS = (1, 2, 3, 5, 8, 13, 20)
# 事前固定阈值：指数过去 20 日累计涨跌 ±2%。选 ±2% 而不是分位数，
# 因为分位数要等样本走完才知道，固定阈值在任意一天都能算。
TH = 0.02


def load_idx_close(code=IDX_CODE):
    out = {}
    for p in sorted(glob.glob(os.path.join(IDX_DIR, "*.parquet"))):
        import pandas as pd
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "close"])
        d = d[d["ts_code"] == code]
        for dt, c in zip(d["trade_date"].astype(str), d["close"].astype(float)):
            if math.isfinite(c) and c > 0:
                out[dt.replace("-", "")[:8]] = c
    return dict(sorted(out.items()))


def mom_map(cl, n=20):
    ds = list(cl)
    return {ds[i]: cl[ds[i]] / cl[ds[i - n]] - 1.0 for i in range(n, len(ds))}


def net_of(c0, c1):
    return (c1 * (1.0 - SELL_COST)) / (c0 * (1.0 + BUY_COST)) - 1.0


def stats(vals_by_day, by_pick=False, raw=None):
    if by_pick:
        y = np.array(raw, float)
    else:
        y = np.array([np.mean(v) for _, v in sorted(vals_by_day.items())], float)
    if len(y) < 3:
        return None
    m = float(y.mean())
    sd = float(y.std(ddof=1))
    t = m / (sd / math.sqrt(len(y))) if sd > 0 else None
    o = np.sort(y)[::-1]
    return {"n": int(len(y)), "mean_pct": round(m * 100, 3),
            "win_pct": round(float((y > 0).mean()) * 100, 1),
            "t": round(t, 2) if t is not None else None,
            "drop5": round(float(o[5:].mean()) * 100, 3) if len(o) > 8 else None,
            "_y": y}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    say = (lambda *x, **k: None) if a.json else print

    recs = [json.loads(x) for x in open(SHADOW, encoding="utf-8") if x.strip()]
    passed = [r for r in recs if r.get("pass") and r.get("mature")]
    P = load_price_panel(sorted({r["code"] for r in passed}), TDX)
    cl = load_idx_close()
    mom = mom_map(cl, 20)

    rows = []
    for r in passed:
        b = P.get(r["code"])
        i = b["pos"].get(r["entry_date"]) if b else None
        m = mom.get(r["entry_date"])
        if i is None or m is None:
            continue
        rec = {"d": r["entry_date"], "type": r["type"], "m": m, "ei": i,
               "code": r["code"], "sigdens": r["sigdens"]}
        for h in HS:
            j = min(i + h, len(b["d"]) - 1)
            if j > i:
                rec["h%d" % h] = net_of(b["c"][i], b["c"][j])
        rows.append(rec)
    say("可算 %d 笔｜%d 天｜势阈值 ±%.0f%%（指数20日累计）" % (len(rows),
        len({r["d"] for r in rows}), TH * 100))

    def reg(m):
        return "弱" if m < -TH else ("强" if m > TH else "中")

    for r in rows:
        r["reg"] = reg(r["m"])
    cnt = {k: len({r["d"] for r in rows if r["reg"] == k}) for k in ("弱", "中", "强")}
    say("分档天数：弱 %d / 中 %d / 强 %d" % (cnt["弱"], cnt["中"], cnt["强"]))

    res = {"th": TH, "n": len(rows), "days": cnt, "matrix": {}, "pick": {}, "month": {}}

    # ── 1) 势 × 持有期 矩阵（日批等权）──
    say("\n【一】势 × 持有期：日批等权均值（含 t）")
    say("  %-4s %s" % ("势", "".join("%14s" % ("h=%d" % h) for h in HS)))
    for lab in ("弱", "中", "强"):
        sub = [r for r in rows if r["reg"] == lab]
        cells = []
        res["matrix"][lab] = {}
        for h in HS:
            by = defaultdict(list)
            for r in sub:
                v = r.get("h%d" % h)
                if v is not None:
                    by[r["d"]].append(v)
            s = stats(by)
            res["matrix"][lab]["h%d" % h] = {k: v for k, v in (s or {}).items() if k != "_y"}
            cells.append("%+7.2f(%+5.2f)" % (s["mean_pct"], s["t"] or 0) if s else "      -      ")
        say("  %-4s %s" % (lab, " ".join(cells)))

    # ── 2) 同一张表换成按笔等权，防加权假象 ──
    say("\n【二】同上，按笔等权（若符号翻转则说明日批加权在作假）")
    say("  %-4s %s" % ("势", "".join("%14s" % ("h=%d" % h) for h in HS)))
    for lab in ("弱", "中", "强"):
        sub = [r for r in rows if r["reg"] == lab]
        cells = []
        res["pick"][lab] = {}
        for h in HS:
            by = defaultdict(list)
            raw = []
            for r in sub:
                v = r.get("h%d" % h)
                if v is not None:
                    by[r["d"]].append(v)
                    raw.append(v)
            s = stats(by, True, raw)
            res["pick"][lab]["h%d" % h] = {k: v for k, v in (s or {}).items() if k != "_y"}
            cells.append("%+7.2f(%+5.2f)" % (s["mean_pct"], s["t"] or 0) if s else "      -      ")
        say("  %-4s %s" % (lab, " ".join(cells)))

    # ── 3) 交互项检验：h=3 的 (强−弱) 与 h=20 的 (强−弱) ──
    say("\n【三】交互项：同是「强市 − 弱市」，短持有与长持有方向是否相反")
    inter = {}
    for h in (3, 5, 20):
        by_s, by_w = defaultdict(list), defaultdict(list)
        for r in rows:
            v = r.get("h%d" % h)
            if v is None:
                continue
            if r["reg"] == "强":
                by_s[r["d"]].append(v)
            elif r["reg"] == "弱":
                by_w[r["d"]].append(v)
        ys = np.array([np.mean(v) for _, v in sorted(by_s.items())], float)
        yw = np.array([np.mean(v) for _, v in sorted(by_w.items())], float)
        diff = ys.mean() - yw.mean()
        # Welch
        se = math.sqrt(ys.var(ddof=1) / len(ys) + yw.var(ddof=1) / len(yw))
        tt = diff / se if se > 0 else None
        inter["h%d" % h] = {"strong_pct": round(float(ys.mean()) * 100, 3),
                            "weak_pct": round(float(yw.mean()) * 100, 3),
                            "diff_pp": round(float(diff) * 100, 3),
                            "t_welch": round(float(tt), 2) if tt else None,
                            "n_s": int(len(ys)), "n_w": int(len(yw))}
        say("  h=%-2d 强 %+6.2f%%  弱 %+6.2f%%  差 %+6.2fpp  t(≈)=%+5.2f"
            % (h, ys.mean() * 100, yw.mean() * 100, diff * 100, tt or 0))
    res["interaction"] = inter

    # ── 4) 逐月：强市短持是否每月都成立 ──
    say("\n【四】逐月：强市 h=3 − 强市 h=20（看交互是不是一个月撑起来的）")
    bym = defaultdict(list)
    for r in rows:
        if r["reg"] == "强":
            bym[r["d"][:6]].append(r)
    say("  %-8s %4s %9s %9s %8s" % ("月份", "天", "h=3", "h=20", "差"))
    for mth in sorted(bym):
        s3, s20 = defaultdict(list), defaultdict(list)
        for r in bym[mth]:
            if r.get("h3") is not None:
                s3[r["d"]].append(r["h3"])
            if r.get("h20") is not None:
                s20[r["d"]].append(r["h20"])
        a3 = stats(s3)
        a20 = stats(s20)
        if not (a3 and a20):
            continue
        res["month"][mth] = {"days": a3["n"], "h3": a3["mean_pct"], "h20": a20["mean_pct"]}
        say("  %-8s %4d %+8.2f%% %+8.2f%% %+7.2f"
            % (mth, a3["n"], a3["mean_pct"], a20["mean_pct"], a3["mean_pct"] - a20["mean_pct"]))
    ok = sum(1 for v in res["month"].values() if v["h3"] > v["h20"])
    say("  h=3 优于 h=20 的月份：%d / %d" % (ok, len(res["month"])))
    res["month_win"] = [ok, len(res["month"])]

    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    if not a.json:
        print("\nwritten -> %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
