# -*- coding: utf-8 -*-
"""P0R：事件研究 + 事件×估值分位组合回测。

数据：curated `anns_d`（2019-2026 全市场公告，去重后按标题规则分类）+ 行情 + 000300 基准；
方法：
  1) 事件研究：事件后下一交易日收盘买入，持有 20/60 交易日，相对 000300 超额；
     按类别统计 n / 均值 / 胜率 / t 值，训练 2019-2023 / 验证 2024-2026；
  2) 组合回测：月末调仓，候选=近 20 个交易日有"正超额事件"+估值历史分位（PS 5y）<50；
     与 event_only / cheap_only / baseline 对比，20 日持有，等权。
输出：JSON + Markdown；判定 = 验证段「事件+估值」月均超额>0 且 t>1.5 且优于两者单独。
"""
import argparse
import json
import os
import time

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from . import backtest as bt
from . import config, events as events_mod

ANN = os.path.join(config.CURATED, "anns_d.parquet")
START, END = "20190101", "20260831"


def load_universe(n):
    cand, _ = bt.build_universe(n)
    d, codes = bt.load_prices(cand)
    piv = d.pivot(index="trade_date", columns="ts_code", values="close").sort_index()
    return d, codes, piv


def load_anns(codes):
    t = pq.read_table(ANN, columns=["ann_date", "ts_code", "title"],
                      filters=[("ts_code", "in", list(codes))])
    a = t.to_pandas()
    a["ann_date"] = a["ann_date"].astype(str)
    a = a[(a["ann_date"] >= START) & (a["ann_date"] <= END)]
    a["title"] = a["title"].astype(str).str.replace(r"^[^：]{2,10}：", "", regex=True)
    a = a.drop_duplicates(["ts_code", "ann_date", "title"])
    a["category"] = a["title"].map(lambda x: events_mod.classify(x))
    a = a[a["title"].map(lambda x: bool(events_mod.MATERIAL.search(x)))]
    return a.sort_values("ann_date").reset_index(drop=True)


def event_study(a, piv, bench, dates, dates_int, cnp):
    col = {c: i for i, c in enumerate(piv.columns)}
    a = a[a["ts_code"].isin(col)].copy()
    ci = a["ts_code"].map(col).to_numpy()
    ann = a["ann_date"].astype(np.int64).to_numpy()
    ei = np.searchsorted(dates_int, ann, side="right")
    ok = ei + 60 < len(dates) - 1
    a = a[ok].reset_index(drop=True)
    ci = a["ts_code"].map(col).to_numpy()
    ei = np.searchsorted(dates_int, a["ann_date"].astype(np.int64).to_numpy(), side="right")

    p0 = cnp[ei, ci]
    p20 = cnp[ei + 20, ci]
    p60 = cnp[ei + 60, ci]
    b0 = bench[ei]
    b20 = bench[ei + 20]
    b60 = bench[ei + 60]
    a["ex20"] = p20 / p0 - b20 / b0
    a["ex60"] = p60 / p0 - b60 / b0
    # P0R 修正：扣除同期全样本等权收益，避免"当前市值前N"幸存者漂移被当成事件超额
    ret20_all = cnp[20:] / cnp[:-20] - 1.0
    ret60_all = cnp[60:] / cnp[:-60] - 1.0
    uni20 = np.nanmean(ret20_all, axis=1)
    uni60 = np.nanmean(ret60_all, axis=1)
    a["rel20"] = (p20 / p0 - 1.0) - uni20[np.clip(ei, 0, len(uni20) - 1)]
    a["rel60"] = (p60 / p0 - 1.0) - uni60[np.clip(ei, 0, len(uni60) - 1)]
    a = a[np.isfinite(a["ex20"])]
    a["period"] = np.where(a["ann_date"] < "20240101", "train", "val")

    def agg(g):
        n = len(g)
        m = g["ex20"].mean()
        m_rel = g["rel20"].mean()
        s = g["ex20"].std(ddof=1) if n > 2 else np.nan
        s_rel = g["rel20"].std(ddof=1) if n > 2 else np.nan
        return {"n": int(n),
                "ex20": round(float(m) * 100, 2),
                "rel20": round(float(m_rel) * 100, 2),
                "win20": round(float((g["ex20"] > 0).mean()) * 100, 1),
                "win_rel20": round(float((g["rel20"] > 0).mean()) * 100, 1),
                "t20": round(float(m / (s / np.sqrt(n))), 2) if n > 2 and s and s > 0 else None,
                "t_rel20": round(float(m_rel / (s_rel / np.sqrt(n))), 2) if n > 2 and s_rel and s_rel > 0 else None,
                "ex60": round(float(g["ex60"].mean()) * 100, 2),
                "rel60": round(float(g["rel60"].mean()) * 100, 2),
                "win60": round(float((g["ex60"] > 0).mean()) * 100, 1)}
    return a, agg


def portfolio(a, piv, bench, dates, hist_pct):
    """月末调仓：event_only / cheap_only / event_cheap / baseline。"""
    close = piv.to_numpy()
    bench = np.asarray(bench, dtype=float)
    codes = list(piv.columns)
    col = {c: i for i, c in enumerate(codes)}
    # 事件日期索引与类别（只保留训练段里表现好的类别后续动态选，这里先给全集）
    a = a.copy()
    a["di"] = np.searchsorted(np.asarray(dates, dtype=np.int64),
                              a["ann_date"].astype(np.int64).to_numpy(), side="right")
    a = a[a["ts_code"].isin(col)]
    a["ci"] = a["ts_code"].map(col)
    pos_cats = ["回购", "业绩", "重组并购", "股权激励", "减持/增持", "重整"]
    ev = a[a["category"].isin(pos_cats)]
    # 月末日期（3/6/9/12 也含，此处用全部月末）
    months = [i for i in range(len(dates) - 1) if dates[i][4:6] != dates[i + 1][4:6]] if len(dates) > 1 else []
    months = [i for i in months if dates[i] >= "20190131" and dates[i] <= "20260831"]
    res = {k: {"train": [], "val": []} for k in ("event_only", "cheap_only", "event_cheap", "baseline")}
    for i in months:
        if i + 20 >= len(dates):
            continue
        fwd = close[i + 20] / close[i] - 1.0
        b = bench[i + 20] / bench[i] - 1.0
        ex = fwd - b
        # 估值便宜集合
        cheap = set()
        if hist_pct is not None:
            hp = hist_pct.loc[dates[i]] if dates[i] in hist_pct.index else None
            if hp is not None:
                cheap = {c for c in codes if pd.notna(hp.get(c)) and float(hp.get(c)) < 50}
        # 近 20 交易日事件集合
        evs = ev[(ev["di"] > i - 20) & (ev["di"] <= i)]
        ev_codes = set(evs["ts_code"].tolist())
        per = "train" if dates[i] < "20240101" else "val"
        for name, sel in (("event_only", ev_codes), ("cheap_only", cheap),
                          ("event_cheap", ev_codes & cheap),
                          ("baseline", set(codes))):
            idx = [col[c] for c in sel if np.isfinite(ex[col[c]])]
            if len(idx) < 5:
                continue
            res[name][per].append(float(np.mean(ex[idx])))
    def summ(v):
        v = np.asarray(v)
        if len(v) < 6:
            return {"months": int(len(v)), "mean": None, "win": None, "t": None}
        m = v.mean()
        t = m / (v.std(ddof=1) / np.sqrt(len(v))) if v.std(ddof=1) > 0 else None
        return {"months": int(len(v)), "mean": round(float(m) * 100, 2),
                "win": round(float((v > 0).mean()) * 100, 1),
                "t": round(float(t), 2) if t else None}
    out = {}
    for k, v in res.items():
        out[k] = {"train": summ(v["train"]), "val": summ(v["val"])}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", type=int, default=500)
    ap.add_argument("--out", default=r"C:\项目\deliverables\2026-10-30_事件驱动组合")
    a = ap.parse_args()
    t0 = time.time()
    d, codes, piv = load_universe(a.universe)
    dates = list(piv.index)
    dates_int = np.asarray([int(x) for x in dates], dtype=np.int64)
    print("[data] universe=%d dates=%d %s→%s" % (len(codes), len(dates), dates[0], dates[-1]), flush=True)

    bench_df = pq.read_table(os.path.join(config.CURATED, "index_daily.parquet"),
                             columns=["ts_code", "trade_date", "close"],
                             filters=[("ts_code", "=", "000300.SH")]).to_pandas()
    bench_df["trade_date"] = bench_df["trade_date"].astype(str)
    bench = bench_df.set_index("trade_date")["close"].reindex(dates).ffill().to_numpy()

    ev_raw = load_anns(codes)
    print("[events] material rows=%d codes=%d" % (len(ev_raw), ev_raw["ts_code"].nunique()), flush=True)
    cnp = piv.to_numpy()
    ev, agg = event_study(ev_raw, piv, bench, dates, dates_int, cnp)
    print("[events] usable=%d" % len(ev), flush=True)
    study = {}
    for cat, g in ev.groupby("category"):
        study[cat] = {p: agg(sub) for p, sub in g.groupby("period")}
    _, hist_pct = bt.load_valuation(codes, [])
    port = portfolio(ev_raw, piv, bench, dates, hist_pct)
    print("[portfolio] done", flush=True)

    summary = {
        "universe": len(codes), "dates": len(dates), "event_rows": int(len(ev)),
        "event_study": study, "portfolio": port,
        "verdict_criterion": "验证段 event_cheap 月均超额>0 且 t>1.5 且优于 event_only/cheap_only",
    }
    v = port.get("event_cheap", {}).get("val", {})
    eo = port.get("event_only", {}).get("val", {})
    co = port.get("cheap_only", {}).get("val", {})
    passed = bool((v.get("mean") or 0) > 0 and (v.get("t") or 0) > 1.5
                  and (v.get("mean") or 0) > (eo.get("mean") or -9)
                  and (v.get("mean") or 0) > (co.get("mean") or -9))
    summary["verdict"] = {"event_cheap_val": v, "event_only_val": eo, "cheap_only_val": co,
                          "passed": passed}
    os.makedirs(a.out, exist_ok=True)
    day = time.strftime("%Y%m%d")
    jp = os.path.join(a.out, "event_backtest_%s.json" % day)
    json.dump(summary, open(jp, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps({"out": jp, "verdict": summary["verdict"]}, ensure_ascii=False, indent=1)[:1800], flush=True)


if __name__ == "__main__":
    main()
