# -*- coding: utf-8 -*-
"""探针2：全信号流扫描，抓 lag>450 的不可能事件（解剖窗口滑移伪信号）"""

import sys
import os
import json
import pandas as pd

BASE = r"C:/项目/老田的AI助手"
DATA = os.path.join(BASE, "quant_backtest", "data", "ashare")
POOL_SRC = os.path.join(BASE, "k_stock", "data", "poc_adapter_chanlun_20261012.json")
sys.path.insert(0, os.path.join(BASE, "quant_backtest"))
from chanlun_signal import analyze as chan_analyze  # noqa

WARMUP = 250
pool = json.load(open(POOL_SRC, encoding="utf-8"))
stocks = sorted({x["stock"] for x in pool})
books = {}
for s in stocks:
    f = os.path.join(DATA, s.replace(".", "_") + ".parquet")
    if os.path.exists(f):
        books[s] = pd.read_parquet(f).sort_values("trade_date").reset_index(drop=True)
cal = sorted(set().union(*[set(b["trade_date"]) for b in books.values()]))
cal = [d for d in cal if d >= "20251013"]


def run_day(day):
    out = {}
    for s, b in books.items():
        seg = b[b.trade_date <= day]
        if len(seg) < WARMUP // 2:
            continue
        bars = [
            {
                "dt": int(r.trade_date),
                "o": float(r.open),
                "h": float(r.high),
                "l": float(r.low),
                "c": float(r.close),
            }
            for r in seg.tail(WARMUP).itertuples()
        ]
        for sg in (chan_analyze(bars) or {}).get("signals") or []:
            out[(s, sg["type"])] = int(sg["dt"])
    return out


prev = run_day("20251009")
weird, all_lags = [], []
for day in cal:
    cur = run_day(day)
    for key in cur.keys() - prev.keys():
        dt = cur[key]
        lag = int(day) - dt
        all_lags.append(lag)
        if lag > 450:
            s = key[0]
            b = books[s]
            seg = b[b.trade_date <= day]
            wstart = seg.tail(WARMUP)["trade_date"].iloc[0]
            weird.append((lag, day, s, key[1], dt, wstart, len(seg)))
    prev = cur
all_lags.sort()
print("全流 fresh 信号数:", len(all_lags))
print(
    "滞后 median:",
    all_lags[len(all_lags) // 2],
    "p90:",
    all_lags[int(len(all_lags) * 0.9)],
    "max:",
    all_lags[-1],
)
print("lag>450 事件数:", len(weird))
for w in sorted(weird, reverse=True)[:20]:
    print("lag=%6d day=%s %s %s sig_dt=%d 窗口起点=%s bars=%d" % w)
