# -*- coding: utf-8 -*-
"""择时层（第二版）：入场点位择不出来，那持有期能不能择（K-RES-20261108-TERM）。

上一轮把「择时」当成「找更好的入场价」，`entry_timing_lab.py` 试了七种规则
（次日开盘、等回踩、等触 ZG、缩量回踩……）**全部为负**，最好的是"次日开盘
无条件买"−3.06%。这说明在这套信号上，任何"等更便宜的价格"都在等下跌。

但择时不只有价格维度，还有**时间维度**：同一笔信号，持 3 天和持 20 天是
两笔不同的交易。而当前产线把持有期写死成 20 日，这个数字从来没被质疑过。

有一条线索指向它：移动止损把平均持仓从 20 日压到 9 日，日批均从 −2.72%
变成 −0.06%（`combo_shadow_stats.json`）。通常被读成"止损有效"，但也可能
只是"缩短持有有效"——止损的功劳里有一部分属于持有期。

本脚本把这两件事拆开：**只换持有期，不加任何止损**，看日批均怎么走。

判据不只是均值，还要看周转
------------------------
持有 3 天和持有 20 天，日批均不可直接比：前者资金一年周转 80 次，后者 12 次。
所以同时报 `ann`（年化近似）=(1+日批均)^(244/h)−1，这才是同一笔资金的可比收益。

势与期限的交互
--------------
如果"势"真的有用，它最可能的用处不是判方向（已证伪），而是**定持有期**：
弱市里结构撑不住 20 天，只能快进快出；强市里才配得上长持。所以按入场当日的
指数 20 日收益分三档，看每档的最优持有期是否不同。

用法:
    python tools/research/hold_term_lab.py
    python tools/research/hold_term_lab.py --json
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
OUT = os.path.join(ROOT, "portal", "hold_term_lab.json")

IDX_CODE = "000985.CSI"
HS = (1, 2, 3, 5, 8, 10, 15, 20)
TRADE_YEAR = 244.0


def net_at(b, i, h):
    """从下标 i 持 h 个交易日，按收盘结算的净收益（含双边成本）。"""
    if b is None or i is None:
        return None
    j = min(i + h, len(b["d"]) - 1)
    if j <= i:
        return None
    c0, c1 = b["c"][i], b["c"][j]
    if not (c0 > 0 and c1 > 0):
        return None
    return (c1 * (1.0 - SELL_COST)) / (c0 * (1.0 + BUY_COST)) - 1.0


def load_index_series(code=IDX_CODE):
    """返回 {8位日期: close}，用复权前的 index close 算 20 日动量。"""
    out = {}
    for p in sorted(glob.glob(os.path.join(IDX_DIR, "*.parquet"))):
        import pandas as pd
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "close"])
        d = d[d["ts_code"] == code]
        for dt, c in zip(d["trade_date"].astype(str), d["close"].astype(float)):
            if math.isfinite(c) and c > 0:
                out[dt.replace("-", "")[:8]] = c
    return dict(sorted(out.items()))


def idx_mom(idx, n=20):
    """每日的指数过去 n 日收益（事前可见：建仓当日收盘即可算）。"""
    ds = list(idx)
    out = {}
    for i in range(len(ds)):
        if i >= n:
            out[ds[i]] = idx[ds[i]] / idx[ds[i - n]] - 1.0
    return out


def stats_by_day(rows, field):
    """rows 是笔级 [{entry_date, val}]，先按日等权，再对日序列做统计。"""
    by = defaultdict(list)
    for r in rows:
        v = r.get(field)
        if v is not None:
            by[r["entry_date"]].append(v)
    if not by:
        return None
    y = np.array([float(np.mean(v)) for _, v in sorted(by.items())], float)
    if len(y) < 3:
        return None
    m = float(y.mean())
    t = float(m / (y.std(ddof=1) / math.sqrt(len(y)))) if y.std(ddof=1) > 0 else None
    o = np.sort(y)[::-1]
    tr = {}
    for k in (1, 3, 5):
        if len(o) > k + 5:
            tr["drop%d" % k] = round(float(o[k:].mean()) * 100, 3)
    return {"n_days": int(len(y)), "mean_pct": round(m * 100, 3),
            "med_pct": round(float(np.median(y)) * 100, 3),
            "win_pct": round(float((y > 0).mean()) * 100, 1),
            "t": round(t, 2) if t is not None else None, "trim": tr, "_y": y}


def ann_of(mean_pct, h):
    """同一笔资金在持有期 h 下的年化近似。

    每天投 1/h 权益、持 h 日 → 一年周转 244/h 轮。这是"日批均"换算成
    "年化"的唯一可比方式，不含复利路径依赖，但与账户 cagr 同量级。
    """
    try:
        return (1.0 + mean_pct / 100.0) ** (TRADE_YEAR / h) - 1.0
    except (OverflowError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    say = (lambda *x, **k: None) if a.json else print

    recs = [json.loads(x) for x in open(SHADOW, encoding="utf-8") if x.strip()]
    passed = [r for r in recs if r.get("pass") and r.get("mature")]
    codes = sorted({r["code"] for r in passed})
    say("信号 %d 笔｜%d 只票｜载入价格面板…" % (len(passed), len(codes)))
    P = load_price_panel(codes, TDX)

    idx = load_index_series()
    mom = idx_mom(idx, 20)

    # 把每天每笔的 h 日净收益算出来
    rows = []
    miss = 0
    for r in passed:
        b = P.get(r["code"])
        i = b["pos"].get(r["entry_date"]) if b else None
        if i is None:
            miss += 1
            continue
        rec = {"entry_date": r["entry_date"], "type": r["type"], "sigdens": r["sigdens"],
               "mom20": mom.get(r["entry_date"])}
        for h in HS:
            rec["h%d" % h] = net_at(b, i, h)
        rows.append(rec)
    say("可算 %d 笔（缺 %d 笔），交易日 %d 天"
        % (len(rows), miss, len({r["entry_date"] for r in rows})))

    res = {"asof": max(r["entry_date"] for r in recs), "n": len(rows), "hs": list(HS),
           "by_h": {}, "by_regime": {}, "by_h_trim": {}}

    # ── 1) 只换持有期 ──
    say("\n【一】只换持有期，不加止损（日批等权，含双边成本 20bp）")
    say("  %3s %6s %6s %6s %6s %8s %9s" %
        ("h", "日数", "日批均", "中位", "胜率", "t", "年化近似"))
    best = None
    for h in HS:
        s = stats_by_day(rows, "h%d" % h)
        if not s:
            continue
        s.pop("_y", None)
        s["ann_pct"] = round(ann_of(s["mean_pct"], h) * 100, 1)
        res["by_h"]["h%d" % h] = s
        say("  %3d %6d %+6.2f%% %+6.2f%% %5.1f%% %+6.2f %+8.1f%%"
            % (h, s["n_days"], s["mean_pct"], s["med_pct"], s["win_pct"],
               s["t"] or 0, s["ann_pct"]))
        if best is None or s["mean_pct"] > best[1]:
            best = (h, s["mean_pct"])

    # ── 2) 稳健性：每个 h 剔掉最好 1/3/5 天 ──
    say("\n【二】稳健性：剔掉最好的 1/3/5 天后还剩多少")
    say("  %3s %9s %9s %9s" % ("h", "drop1", "drop3", "drop5"))
    for h in HS:
        s = res["by_h"].get("h%d" % h)
        if not s:
            continue
        tr = s["trim"]
        res["by_h_trim"]["h%d" % h] = tr
        say("  %3d %9s %9s %9s" % (h, tr.get("drop1"), tr.get("drop3"), tr.get("drop5")))

    # ── 3) 势 × 持有期 ──
    mvals = np.array([r["mom20"] for r in rows if r["mom20"] is not None], float)
    if len(mvals) > 10:
        q1, q2 = float(np.percentile(mvals, 33.3)), float(np.percentile(mvals, 66.7))

        def reg(m):
            if m is None:
                return None
            return "弱" if m <= q1 else ("中" if m <= q2 else "强")

        for r in rows:
            r["reg"] = reg(r["mom20"])
        say("\n【三】势 × 持有期（势=入场当日指数过去20日收益，三分位；阈值 %+.2f%% / %+.2f%%）"
            % (q1 * 100, q2 * 100))
        say("  %-4s %5s %7s %7s %7s" % ("势", "天", "h=3", "h=5", "h=20"))
        for lab in ("弱", "中", "强"):
            sub = [r for r in rows if r.get("reg") == lab]
            cell = {"n_days": len({r["entry_date"] for r in sub})}
            line = []
            for h in (3, 5, 20):
                s = stats_by_day(sub, "h%d" % h)
                cell["h%d" % h] = s["mean_pct"] if s else None
                line.append("%+7.2f" % s["mean_pct"] if s else "      -")
            res["by_regime"][lab] = cell
            say("  %-4s %5d %s" % (lab, cell["n_days"], " ".join(line)))
        say("  注：势分档用的是事后才知道的区间三分位，属于**样本内**，只作方向参考。")

    # ── 4) 与产线现行（固定 20 日）的差距 ──
    s20 = res["by_h"].get("h20")
    if best and s20:
        say("\n【四】结论数字")
        say("  现行固定 20 日：日批均 %+.2f%%，年化近似 %+.1f%%"
            % (s20["mean_pct"], s20["ann_pct"]))
        hb = res["by_h"]["h%d" % best[0]]
        say("  最优 h=%d：日批均 %+.2f%%，年化近似 %+.1f%%"
            % (best[0], hb["mean_pct"], hb["ann_pct"]))
        res["conclusion"] = {"h_now": 20, "mean_now": s20["mean_pct"],
                             "h_best": best[0], "mean_best": hb["mean_pct"],
                             "ann_now": s20["ann_pct"], "ann_best": hb["ann_pct"]}

    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    if not a.json:
        print("\nwritten -> %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
