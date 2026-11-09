# -*- coding: utf-8 -*-
"""择势层的重做：把「势」从外部指数换成池子自己的产出密度（K-RES-20261108-CROWD）。

为什么重做
----------
上一轮 `regime_robust.py` 把「势」定义成指数的中枢位置 / 中枢方向 / 均线排列，
八个判据**全部反向**（中枢下移 +5.73%、空头排列 +4.01%），剔掉 2024-09 那一个
十天窗口后 t 值从 +2.50 掉到 −16.14。结论是：**指数判不了方向**。

但「判不了方向」不等于「势没用」。这一轮换一个定义：

    势 = 池子当天自己产出了多少买点

缠论讲「走势必完美」，个股的买点是**个股结构**的事；可当一天里有 104 只票同时
冒出买点、而平时只有 6 只时，那天发生的不是 104 个独立的结构机会，而是**一次
系统性下跌把所有票同时推进了"看起来像买点"的位置**。这是结构噪声，不是结构
机会。用指数看这件事是隔了一层；用池子自己的产出密度看，是内生的、第一时间
可见的、不依赖任何外部判据的。

必须先证明它不是指数的影子
--------------------------
拥挤日和暴跌日高度重合，所以「拥挤日收益差」很可能只是「跌了」的另一种说法。
若如此，这个指标没有增量信息，等于把指数换个马甲。

所以先做一个控制变量回归：

    日批收益 = a + b1 * 当日指数收益 + b2 * log1p(信号数) + e

b2 显著为负 → 拥挤度在控制住指数之后仍有独立解释力，可以作为择势闸门。
b2 不显著   → 它只是指数的代理，择势这一层仍然没有独立信息，如实报告。

口径纪律（沿用 asof_20261106.py）
--------------------------------
1. 一律日批等权：先把同一天几十笔压成一个日均值，再对日序列做统计。
2. 一律报「剔掉最好 N 天」：样本只有 88 个开仓日，任何均值都要看它靠几天撑着。
3. 账户层数字只作参考，且必须同时打 util —— 不同 util 的 cagr 不可横向比。

用法:
    python tools/research/crowd_gate.py
    python tools/research/crowd_gate.py --json
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

SHADOW = os.path.join(ROOT, "quant_backtest", "data", "combo_shadow.jsonl")
IDX_DIR = os.path.join(ROOT, "quant_backtest", "data", "tdx", "ts_index_daily")
OUT = os.path.join(ROOT, "portal", "crowd_gate.json")

IDX_CODE = "000985.CSI"     # 中证全指 = 全市场，与 idx_ret 同源


# ─────────────────────────── 基础统计 ───────────────────────────
def wmean_t(y, w):
    """加权均值与 t 值。w 为权重（全 1 时退化为普通均值）。

    有效样本量用 Kish 公式 n_eff = (Σw)²/Σw²，避免把「权重 0.2 的日子」
    当成满样本计入，把 t 值虚高。
    """
    y = np.asarray(y, float)
    w = np.asarray(w, float)
    sw = w.sum()
    if sw <= 0 or len(y) < 3:
        return None, None, 0.0
    m = float((w * y).sum() / sw)
    var = float((w * (y - m) ** 2).sum() / sw)
    n_eff = float(sw ** 2 / (w ** 2).sum())
    if var <= 0 or n_eff < 3:
        return m, None, n_eff
    return m, m / math.sqrt(var / n_eff), n_eff


def ols(y, X, names):
    """普通最小二乘 + 经典标准误。X 不含常数项，这里自动加。"""
    y = np.asarray(y, float)
    X = np.asarray(X, float)
    if X.ndim == 1:
        X = X[:, None]
    A = np.column_stack([np.ones(len(X))] + [X[:, j] for j in range(X.shape[1])])
    beta, *_ = np.linalg.lstsq(A, y, rcond=None)
    resid = y - A @ beta
    n, k = A.shape
    dof = max(n - k, 1)
    s2 = float(resid @ resid) / dof
    try:
        cov = s2 * np.linalg.inv(A.T @ A)
    except np.linalg.LinAlgError:
        return {"names": names, "beta": beta.tolist(), "t": [None] * k, "r2": None}
    se = np.sqrt(np.clip(np.diag(cov), 0, None))
    t = [float(beta[i] / se[i]) if se[i] > 0 else None for i in range(k)]
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - float(resid @ resid) / ss_tot if ss_tot > 0 else None
    return {"names": ["const"] + list(names), "beta": [float(b) for b in beta],
            "t": t, "r2": round(r2, 4) if r2 is not None else None, "n": n}


def trim(y, w, ks=(1, 3, 5, 10)):
    """剔掉日批收益最高的 k 天后重算。返回 {k: (mean, t)}。"""
    y = np.asarray(y, float)
    w = np.asarray(w, float)
    order = np.argsort(y)[::-1]
    out = {}
    for k in ks:
        if len(y) > k + 5:
            keep = order[k:]
            m, t, _ = wmean_t(y[keep], w[keep])
            out["drop%d" % k] = {"mean_pct": round(m * 100, 3) if m is not None else None,
                                 "t": round(t, 2) if t is not None else None}
    return out


# ─────────────────────────── 数据装载 ───────────────────────────
def load_index(code=IDX_CODE):
    """返回 {8位日期: 当日涨跌幅(小数)}。"""
    out = {}
    for p in sorted(glob.glob(os.path.join(IDX_DIR, "*.parquet"))):
        import pandas as pd
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "close", "pct_chg"])
        d = d[d["ts_code"] == code]
        for dt, pc in zip(d["trade_date"].astype(str), d["pct_chg"].astype(float)):
            if math.isfinite(pc):
                out[dt.replace("-", "")[:8]] = pc / 100.0
    return out


def build_days(recs, idx):
    """把笔级清单压成日批序列。"""
    by = defaultdict(list)
    for r in recs:
        by[r["entry_date"]].append(r)
    days = []
    for d in sorted(by):
        rs = by[d]
        days.append({
            "date": d,
            "n_sig": len(rs),
            "hold": float(np.mean([r["hold_ret"] for r in rs])),
            "trail": float(np.mean([r["trail_ret"] for r in rs])),
            "idx": idx.get(d),
            "idx_ret_mean": float(np.mean([r["idx_ret"] for r in rs
                                           if r.get("idx_ret") is not None] or [0.0])),
            "sigdens_med": float(np.median([r["sigdens"] for r in rs])),
            "n_1": sum(1 for r in rs if r["type"] == "1买"),
            "n_2": sum(1 for r in rs if r["type"] == "2买"),
        })
    return days


# ─────────────────────────── 权重方案 ───────────────────────────
def make_weights(days, scheme):
    """按方案给出每日权重。全部只用**事前可见**的量。

    事前可见的定义：信号是当日收盘后确认的（detect_date），当日信号数、
    当日指数涨跌在建仓那一刻（次日开盘）都是已知的。不存在未来函数。
    """
    n = np.array([d["n_sig"] for d in days], float)
    ix = np.array([d["idx"] if d["idx"] is not None else 0.0 for d in days], float)
    p70 = float(np.percentile(n, 70))
    p85 = float(np.percentile(n, 85))

    if scheme == "W0_const":
        return np.ones(len(n))
    if scheme == "W1_crowd_half":           # 拥挤（>p70）减半
        return np.where(n > p70, 0.5, 1.0)
    if scheme == "W2_crowd_off":            # 拥挤（>p85）当日不做
        return np.where(n > p85, 0.0, 1.0)
    if scheme == "W3_crowd_inv":            # 连续逆权：w = clip(median/n, .2, 1)
        med = float(np.median(n))
        return np.clip(med / np.maximum(n, 1.0), 0.2, 1.0)
    if scheme == "W4_idx_down_half":        # 指数当日跌超 1.5% 减半（对照：指数口径）
        return np.where(ix < -0.015, 0.5, 1.0)
    if scheme == "W5_idx_up":               # 反向对照：指数当日涨才做
        return np.where(ix > 0, 1.0, 0.0)
    if scheme == "W6_combo":                # 拥挤减半 + 指数跌减半，取小
        a = np.where(n > p70, 0.5, 1.0)
        b = np.where(ix < -0.015, 0.5, 1.0)
        return np.minimum(a, b)
    raise ValueError(scheme)


SCHEMES = ["W0_const", "W1_crowd_half", "W2_crowd_off", "W3_crowd_inv",
           "W4_idx_down_half", "W5_idx_up", "W6_combo"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--field", default="hold", choices=("hold", "trail"),
                    help="日批收益用哪个出场口径；默认 hold（不受止损触发时点影响）")
    a = ap.parse_args()
    say = (lambda *x, **k: None) if a.json else print

    recs = [json.loads(x) for x in open(SHADOW, encoding="utf-8") if x.strip()]
    passed = [r for r in recs if r.get("pass")]
    mature = [r for r in passed if r.get("mature")]
    say("清单 pass %d 笔｜已到期 %d 笔｜出场口径 %s" % (len(passed), len(mature), a.field))

    idx = load_index()
    days = build_days(mature, idx)
    if not days:
        print("no days", file=sys.stderr)
        return 2
    n = np.array([d["n_sig"] for d in days], float)
    y = np.array([d[a.field] for d in days], float)
    ix = np.array([d["idx"] if d["idx"] is not None else 0.0 for d in days], float)
    say("开仓日 %d 天｜信号数 中位 %.0f / 最大 %.0f｜基线日批均 %+.3f%%"
        % (len(days), np.median(n), n.max(), y.mean() * 100))

    res = {"asof": max(r["entry_date"] for r in recs), "field": a.field,
           "n_passed": len(passed), "n_mature": len(mature), "n_days": len(days),
           "baseline": {}}

    # ── 1) 拥挤度本身：分档 ──
    qs = [float(np.percentile(n, p)) for p in (0, 20, 40, 60, 80, 100)]
    buckets = []
    for i in range(5):
        lo, hi = qs[i], qs[i + 1]
        m = (n > lo) & (n <= hi) if i else (n >= lo) & (n <= hi)
        if m.sum() < 5:
            continue
        buckets.append({"range": [int(lo), int(hi)], "n_days": int(m.sum()),
                        "avg_sig": round(float(n[m].mean()), 1),
                        "ret_pct": round(float(y[m].mean()) * 100, 3),
                        "t": round(wmean_t(y[m], np.ones(int(m.sum())))[1] or 0.0, 2)})
    res["buckets"] = buckets
    say("\n【一】按当日信号数分档（日批等权，%s 口径）" % a.field)
    for b in buckets:
        say("  %2d~%3d 笔/日  %2d 天  均值 %+.3f%%  t=%+.2f"
            % (b["range"][0], b["range"][1], b["n_days"], b["ret_pct"], b["t"]))

    # ── 2) 增量检验：控制指数后拥挤度还有没有解释力 ──
    corr = float(np.corrcoef(n, ix)[0, 1])
    o_full = ols(y, np.column_stack([ix, np.log1p(n)]), ["idx", "log_n"])
    o_idx = ols(y, ix, ["idx"])
    o_crd = ols(y, np.log1p(n)[:, None], ["log_n"])
    res["incremental"] = {"corr_n_idx": round(corr, 3),
                          "model_idx_only": o_idx, "model_crowd_only": o_crd,
                          "model_both": o_full}
    say("\n【二】增量检验：拥挤日是不是只是「跌得多的那一天」的代理")
    say("  信号数与当日指数收益的相关系数 = %+.3f" % corr)
    say("  仅指数      : b_idx=%+.4f (t=%+.2f)  R²=%.3f"
        % (o_idx["beta"][1], o_idx["t"][1] or 0, o_idx["r2"] or 0))
    say("  仅拥挤度    : b_logN=%+.4f (t=%+.2f)  R²=%.3f"
        % (o_crd["beta"][1], o_crd["t"][1] or 0, o_crd["r2"] or 0))
    say("  两者同时    : b_idx=%+.4f (t=%+.2f)  b_logN=%+.4f (t=%+.2f)  R²=%.3f"
        % (o_full["beta"][1], o_full["t"][1] or 0,
           o_full["beta"][2], o_full["t"][2] or 0, o_full["r2"] or 0))

    # ── 3) 各权重方案的加权日批均 ──
    table = []
    for s in SCHEMES:
        w = make_weights(days, s)
        m, t, neff = wmean_t(y, w)
        tr = trim(y, w)
        table.append({"scheme": s, "w_sum": round(float(w.sum()), 1),
                      "n_eff": round(neff, 1),
                      "mean_pct": round(m * 100, 3) if m is not None else None,
                      "t": round(t, 2) if t is not None else None,
                      "trim": tr,
                      "vs_base_pp": round((m - y.mean()) * 100, 3) if m is not None else None})
    res["schemes"] = table
    say("\n【三】择势闸门：同一天批、只换每日投放权重")
    say("  %-18s %5s %6s %5s %s" % ("方案", "有效日", "日批均", "t", "剔最好1/3/5/10天后"))
    base = y.mean() * 100
    for r in table:
        trs = "/".join("%+.2f" % r["trim"][k]["mean_pct"] if r["trim"][k]["mean_pct"] is not None
                       else "  -  " for k in ("drop1", "drop3", "drop5", "drop10"))
        say("  %-18s %5.1f %+6.3f%% %+5.2f %s" % (r["scheme"], r["n_eff"],
                                                  r["mean_pct"], r["t"] or 0, trs))
    say("  （基线 W0 日批均 = %+.3f%%）" % base)

    # ── 4) 按月看：改善是不是只在某个月 ──
    bym = defaultdict(list)
    for i, d in enumerate(days):
        bym[d["date"][:6]].append(i)
    monthly = {}
    for mth in sorted(bym):
        ii = bym[mth]
        w0 = make_weights([days[i] for i in ii], "W0_const")
        w6 = make_weights([days[i] for i in ii], "W1_crowd_half")
        m0, _, _ = wmean_t(y[ii], w0)
        m6, _, _ = wmean_t(y[ii], w6)
        if m0 is None or m6 is None:
            continue
        monthly[mth] = {"days": len(ii), "base_pct": round(m0 * 100, 3),
                        "w1_pct": round(m6 * 100, 3)}
    res["monthly"] = monthly
    say("\n【四】按月：W1（拥挤减半）相对基线的逐月表现")
    for k, v in monthly.items():
        say("  %s  %2d 天   基线 %+7.3f%%   W1 %+7.3f%%   %+.3fpp"
            % (k, v["days"], v["base_pct"], v["w1_pct"], v["w1_pct"] - v["base_pct"]))

    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    if not a.json:
        print("\nwritten -> %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
