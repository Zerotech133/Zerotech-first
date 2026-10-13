# -*- coding: utf-8 -*-
"""K-STOCK-323 · 双刀 14:45 快照重构器（从 ts_rt_min 分钟线）。

窗口=每日 14:41~14:45（含）五分钟：open=首笔 open、high/low=极值、
close=末笔 close、vol/amount=求和（ts_rt_min 为分钟增量口径，已实测）。
输出每日一行/票的「14:45 口径日 K」→ data/tdx/dknife_snap/dknife_snap.parquet
列：ts_code, trade_date, open, high, low, close, vol, amount, n_ticks。
消费端：backtest_engine_v2.calc_dknife_signals（双刀全市场模式）。

用法：python dknife_snap.py [--verify]
"""
import glob
import os
import sys
import time

import pandas as pd

QBT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(QBT, "data", "tdx", "ts_rt_min")
OUT = os.path.join(QBT, "data", "tdx", "dknife_snap", "dknife_snap.parquet")
WIN_MM = {"14:41", "14:42", "14:43", "14:44", "14:45"}


def build():
    fs = sorted(glob.glob(os.path.join(SRC, "*.parquet")))
    if not fs:
        print("no source parts:", SRC)
        sys.exit(1)
    t0 = time.time()
    win_parts, cum_parts = [], []
    for i, fp in enumerate(fs):
        d = pd.read_parquet(fp, columns=["ts_code", "time", "open", "high", "low", "close", "vol", "amount"])
        d["time"] = d["time"].astype(str)
        hm = d["time"].str[11:16]
        m_win = hm.isin(WIN_MM)
        m_cum = hm <= "14:45"          # 当日截至 14:45 的全部分钟（含开盘竞价后首分钟 09:30）
        if m_win.any():
            win_parts.append(d[m_win])
        if m_cum.any():
            cum_parts.append(d[m_cum][["ts_code", "time", "vol", "amount"]])
        if (i + 1) % 25 == 0 or i == len(fs) - 1:
            print("scan %d/%d (%.0fs)" % (i + 1, len(fs), time.time() - t0), flush=True)
    w = pd.concat(win_parts, ignore_index=True)
    w["trade_date"] = w["time"].str[:10].str.replace("-", "")
    w = w.sort_values("time")
    g = w.groupby(["ts_code", "trade_date"], sort=True)
    snap = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                 close=("close", "last"), n_ticks=("close", "size")).reset_index()
    # 量额换算到日线湖口径（ts_daily amount=千元、vol=手；ts_rt_min amount=元、vol=股）
    c = pd.concat(cum_parts, ignore_index=True)
    c["trade_date"] = c["time"].str[:10].str.replace("-", "")
    gc = c.groupby(["ts_code", "trade_date"], sort=True).agg(
        vol=("vol", lambda x: x.sum() / 100.0), amount=("amount", lambda x: x.sum() / 1000.0))
    snap = snap.merge(gc.reset_index(), on=["ts_code", "trade_date"], how="left")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    snap.to_parquet(OUT, index=False)
    print("saved:", OUT, os.path.getsize(OUT), "bytes | rows:", len(snap),
          "| days:", snap.trade_date.nunique(), "| codes:", snap.ts_code.nunique())
    return snap


def verify(snap):
    print("\n== verify ==")
    # 1) 每日票数
    per_day = snap.groupby("trade_date").size()
    print("每日票数 min/median/max:", per_day.min(), int(per_day.median()), per_day.max())
    # 2) n_ticks 分布（应≈5，缺失=快照断点）
    print("n_ticks 分布:", snap.n_ticks.value_counts().sort_index().to_dict())
    # 3) close 偏差 vs 日线湖（同日同票）
    daily = pd.read_parquet(os.path.join(QBT, "data", "tdx", "ts_daily"),
                            columns=["ts_code", "trade_date", "close"])
    m = snap.merge(daily, on=["ts_code", "trade_date"], suffixes=("_snap", "_daily"))
    m["dev_pct"] = (m.close_snap - m.close_daily) / m.close_daily * 100
    print("对齐行:", len(m), "/", len(snap),
          "| close偏差 mean={:.3f} abs_median={:.3f} abs_p95={:.3f}".format(
              m.dev_pct.mean(), m.dev_pct.abs().median(), m.dev_pct.abs().quantile(0.95)))
    # 4) amount 量纲 vs 日线全天额（14:45 累计应≈全天 88~95%）
    daily_amt = pd.read_parquet(os.path.join(QBT, "data", "tdx", "ts_daily"),
                                columns=["ts_code", "trade_date", "amount"])
    m3 = snap.merge(daily_amt, on=["ts_code", "trade_date"], suffixes=("_snap", "_daily"))
    m3["ratio"] = m3.amount_snap / m3.amount_daily
    ok = m3[(m3.ratio > 0.5) & (m3.ratio < 1.5)]
    print("amount 对齐行:", len(m3), "| ratio 中位=%.3f p05=%.3f p95=%.3f" % (
        ok.ratio.median(), ok.ratio.quantile(0.05), ok.ratio.quantile(0.95)))
    return m


if __name__ == "__main__":
    if "--verify" in sys.argv and os.path.exists(OUT):
        verify(pd.read_parquet(OUT))
    else:
        s = build()
        verify(s)
