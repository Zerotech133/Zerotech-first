# -*- coding: utf-8 -*-
"""探针：20251013 当日 fresh 信号滞后分布 + 大滞后信号解剖"""
import sys, os, json
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

def run_day(day):
    out = {}
    for s, b in books.items():
        seg = b[b.trade_date <= day]
        if len(seg) < WARMUP // 2:
            continue
        bars = [{"dt": int(r.trade_date), "o": float(r.open), "h": float(r.high),
                 "l": float(r.low), "c": float(r.close)} for r in seg.tail(WARMUP).itertuples()]
        for sg in ((chan_analyze(bars) or {}).get("signals") or []):
            out[(s, sg["type"])] = int(sg["dt"])
    return out

prev = run_day("20251009")
cur = run_day("20251013")
fresh = cur.items() - prev.items()
rows = []
for (s, tp), dt in fresh:
    nbars = int((books[s].trade_date <= "20251013").sum())
    rows.append((20251013 - dt, s, tp, dt, nbars))
rows.sort(reverse=True)
lags = sorted(r[0] for r in rows)
print("fresh 信号数", len(rows), "滞后 median", lags[len(lags)//2], "p90", lags[int(len(lags)*0.9)], "max", lags[-1])
for r in rows[:15]:
    print("lag=%7d %s %s sig_dt=%d bars=%d" % r)
# 滞后分布直方（粗）
import collections
buckets = collections.Counter()
for L in lags:
    if L <= 5: buckets["<=5d"] += 1
    elif L <= 20: buckets["6-20d"] += 1
    elif L <= 60: buckets["21-60d"] += 1
    elif L <= 120: buckets["61-120d"] += 1
    elif L <= 250: buckets["121-250d"] += 1
    else: buckets[">250d(不可能>窗口)"] += 1
print(dict(buckets))
