# -*- coding: utf-8 -*-
"""K-STOCK-323/325 · 双刀 14:45 快照重构器。

窗口=每日 14:41~14:45（含）五分钟：open=首笔 open、high/low=极值、
close=末笔 close、vol/amount=当日累计至 14:45（两源均为分钟增量口径，已实测）。
量额换算到日线湖口径（ts_daily amount=千元、vol=手；两源 amount=元、vol=股）。

源（--src）：
  rtmin    ts_rt_min 分钟湖（125 分片·行切片·覆盖 20260806-0901）
  tushare  tushare stk_mins 1min（按 码_月 分片·覆盖 202509 起·325 全市场回补）
输出每日一行/票的「14:45 口径日 K」→ data/tdx/dknife_snap/dknife_snap.parquet
列：ts_code, trade_date, open, high, low, close, vol, amount, n_ticks。
消费端：backtest_engine_v2.calc_dknife_signals（双刀全市场模式）。

用法：
  python dknife_snap.py --src rtmin [--verify]
  python dknife_snap.py --src tushare --ym0 202606 --ym1 202610 [--verify]
"""

import glob
import os
import sys
import time

import pandas as pd

QBT = os.path.dirname(os.path.abspath(__file__))
SRC_RT = os.path.join(QBT, "data", "tdx", "ts_rt_min")
SRC_TS = os.path.join(QBT, "data", "tushare", "stk_mins")
OUT = os.path.join(QBT, "data", "tdx", "dknife_snap", "dknife_snap.parquet")
WIN_MM = {"14:41", "14:42", "14:43", "14:44", "14:45"}


def _agg(win_parts, cum_parts):
    """窗口 OHLC + 当日累计量额（÷100/÷1000 换算日线湖口径）。"""
    w = pd.concat(win_parts, ignore_index=True)
    w["trade_date"] = w["time"].str[:10].str.replace("-", "")
    w = w.sort_values("time")
    g = w.groupby(["ts_code", "trade_date"], sort=True)
    snap = g.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        n_ticks=("close", "size"),
    ).reset_index()
    c = pd.concat(cum_parts, ignore_index=True)
    c["trade_date"] = c["time"].str[:10].str.replace("-", "")
    gc = c.groupby(["ts_code", "trade_date"], sort=True).agg(
        vol=("vol", lambda x: x.sum() / 100.0),
        amount=("amount", lambda x: x.sum() / 1000.0),
    )
    return snap.merge(gc.reset_index(), on=["ts_code", "trade_date"], how="left")


def build_rtmin():
    fs = sorted(glob.glob(os.path.join(SRC_RT, "*.parquet")))
    if not fs:
        print("no source parts:", SRC_RT)
        sys.exit(1)
    t0 = time.time()
    win_parts, cum_parts = [], []
    for i, fp in enumerate(fs):
        d = pd.read_parquet(
            fp,
            columns=[
                "ts_code",
                "time",
                "open",
                "high",
                "low",
                "close",
                "vol",
                "amount",
            ],
        )
        d["time"] = d["time"].astype(str)
        hm = d["time"].str[11:16]
        m_win = hm.isin(WIN_MM)
        m_cum = hm <= "14:45"
        if m_win.any():
            win_parts.append(d[m_win])
        if m_cum.any():
            cum_parts.append(d[m_cum][["ts_code", "time", "vol", "amount"]])
        if (i + 1) % 25 == 0 or i == len(fs) - 1:
            print("scan %d/%d (%.0fs)" % (i + 1, len(fs), time.time() - t0), flush=True)
    return _agg(win_parts, cum_parts)


def build_tushare(ym0="202606", ym1="202610"):
    """tushare stk_mins 源（列 trade_time·单位同 ts_rt_min：vol=股 amount=元）。"""
    fs = sorted(glob.glob(os.path.join(SRC_TS, "*_*.parquet")))
    fs = [f for f in fs if ym0 <= os.path.basename(f).rsplit("_", 1)[1][:6] <= ym1]
    if not fs:
        print("no source files in [%s, %s]:" % (ym0, ym1), SRC_TS)
        sys.exit(1)
    t0 = time.time()
    win_parts, cum_parts = [], []
    for i, fp in enumerate(fs):
        d = pd.read_parquet(fp)
        if d.empty:
            continue
        d = d.rename(columns={"trade_time": "time"})
        d["time"] = d["time"].astype(str)
        hm = d["time"].str[11:16]
        m_win = hm.isin(WIN_MM)
        m_cum = hm <= "14:45"
        if m_win.any():
            win_parts.append(d[m_win])
        if m_cum.any():
            cum_parts.append(d[m_cum][["ts_code", "time", "vol", "amount"]])
        if (i + 1) % 2500 == 0 or i == len(fs) - 1:
            print("scan %d/%d (%.0fs)" % (i + 1, len(fs), time.time() - t0), flush=True)
    return _agg(win_parts, cum_parts)


def verify(snap):
    print("\n== verify ==")
    # 1) 每日票数
    per_day = snap.groupby("trade_date").size()
    print(
        "每日票数 min/median/max:", per_day.min(), int(per_day.median()), per_day.max()
    )
    # 2) n_ticks 分布（应≈5，缺失=快照断点）
    print("n_ticks 分布:", snap.n_ticks.value_counts().sort_index().to_dict())
    # 3) close 偏差 vs 日线湖（同日同票）
    daily = pd.read_parquet(
        os.path.join(QBT, "data", "tdx", "ts_daily"),
        columns=["ts_code", "trade_date", "close"],
    )
    m = snap.merge(daily, on=["ts_code", "trade_date"], suffixes=("_snap", "_daily"))
    m["dev_pct"] = (m.close_snap - m.close_daily) / m.close_daily * 100
    print(
        "对齐行:",
        len(m),
        "/",
        len(snap),
        "| close偏差 mean={:.3f} abs_median={:.3f} abs_p95={:.3f}".format(
            m.dev_pct.mean(), m.dev_pct.abs().median(), m.dev_pct.abs().quantile(0.95)
        ),
    )
    # 4) amount 量纲 vs 日线全天额（14:45 累计应≈全天 88~95%）
    daily_amt = pd.read_parquet(
        os.path.join(QBT, "data", "tdx", "ts_daily"),
        columns=["ts_code", "trade_date", "amount"],
    )
    m3 = snap.merge(
        daily_amt, on=["ts_code", "trade_date"], suffixes=("_snap", "_daily")
    )
    m3["ratio"] = m3.amount_snap / m3.amount_daily
    ok = m3[(m3.ratio > 0.5) & (m3.ratio < 1.5)]
    print(
        "amount 对齐行:",
        len(m3),
        "| ratio 中位=%.3f p05=%.3f p95=%.3f"
        % (ok.ratio.median(), ok.ratio.quantile(0.05), ok.ratio.quantile(0.95)),
    )
    return m


def verify_cross(snap):
    """与旧 ts_rt_min 快照在重叠日（20260806-0901）交叉验证 close。"""
    old_fp = os.path.join(QBT, "data", "tdx", "dknife_snap", "_old_rtmin_snap.parquet")
    if not os.path.exists(old_fp):
        print("(无旧快照可交叉验证：%s)" % old_fp)
        return
    old = pd.read_parquet(old_fp)
    j = snap.merge(old, on=["ts_code", "trade_date"], suffixes=("_new", "_old"))
    j["dev_pct"] = (j.close_new - j.close_old) / j.close_old * 100
    print(
        "交叉验证（vs 旧 rtmin 快照）重叠行:",
        len(j),
        "| close偏差 mean={:.4f} abs_median={:.4f} abs_p99={:.4f}".format(
            j.dev_pct.mean(), j.dev_pct.abs().median(), j.dev_pct.abs().quantile(0.99)
        ),
    )


if __name__ == "__main__":
    src = "rtmin"
    if "--src" in sys.argv:
        src = sys.argv[sys.argv.index("--src") + 1]
    if "--verify" in sys.argv and os.path.exists(OUT):
        verify(pd.read_parquet(OUT))
    else:
        if src == "tushare":
            ym0 = (
                sys.argv[sys.argv.index("--ym0") + 1]
                if "--ym0" in sys.argv
                else "202606"
            )
            ym1 = (
                sys.argv[sys.argv.index("--ym1") + 1]
                if "--ym1" in sys.argv
                else "202610"
            )
            old_fp = os.path.join(
                QBT, "data", "tdx", "dknife_snap", "_old_rtmin_snap.parquet"
            )
            if os.path.exists(OUT) and not os.path.exists(old_fp):
                import shutil

                shutil.copy2(OUT, old_fp)
                print("旧 rtmin 快照已备份:", old_fp)
            s = build_tushare(ym0, ym1)
        else:
            s = build_rtmin()
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        s.to_parquet(OUT, index=False)
        print(
            "saved:",
            OUT,
            os.path.getsize(OUT),
            "bytes | rows:",
            len(s),
            "| days:",
            s.trade_date.nunique(),
            "| codes:",
            s.ts_code.nunique(),
        )
        verify(s)
        if src == "tushare":
            verify_cross(s)
