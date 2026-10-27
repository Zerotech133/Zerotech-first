# -*- coding: utf-8 -*-
"""探针4（决定性）：确认日入场价相对信号日涨幅分桶 × 前瞻收益
若低涨幅桶 fwd 为正 → 「runup 过滤」可救活策略；否则须改引擎确认逻辑。
口径：全部 fresh 买点（seen-ever 去重后），入场=确认日次一交易日收盘(hfq)，
前瞻 = 入场日→+20交易日 hfq 收益。
"""

import sys
import os
import json
import pandas as pd
import datetime as _d

BASE = r"C:/项目/老田的AI助手"
DATA = os.path.join(BASE, "quant_backtest", "data", "ashare")
POOL_SRC = os.path.join(BASE, "k_stock", "data", "poc_adapter_chanlun_20261012.json")
sys.path.insert(0, os.path.join(BASE, "quant_backtest"))
from chanlun_signal import analyze as chan_analyze  # noqa

WARMUP = 250
pool = json.load(open(POOL_SRC, encoding="utf-8"))
stocks = sorted({x["stock"] for x in pool})
books, adj_close = {}, {}
for s in stocks:
    f = os.path.join(DATA, s.replace(".", "_") + ".parquet")
    if not os.path.exists(f):
        continue
    b = pd.read_parquet(f).sort_values("trade_date").reset_index(drop=True)
    r = b["pct_chg"] / 100.0
    r.iloc[0] = 0.0
    kfac = (1.0 + r).cumprod() / b["close"]
    for _c in ("open", "high", "low", "close"):
        b["adj_" + _c] = b[_c] * kfac
    books[s] = b
    adj_close[s] = b.set_index("trade_date")["adj_close"]

cal = sorted(set().union(*[set(b["trade_date"]) for b in books.values()]))
cal = [d for d in cal if d >= "20251013"]
BUY_TYPES = {"1买", "2买", "3买"}


def run_day(day):
    out = {}
    for s, b in books.items():
        seg = b[b.trade_date <= day]
        if len(seg) < WARMUP // 2:
            continue
        bars = [
            {
                "dt": int(r.trade_date),
                "o": float(r.adj_open),
                "h": float(r.adj_high),
                "l": float(r.adj_low),
                "c": float(r.adj_close),
            }
            for r in seg.tail(WARMUP).itertuples()
        ]
        for sg in (chan_analyze(bars) or {}).get("signals") or []:
            out[(s, sg["type"])] = int(sg["dt"])
    return out


def lag_days(day, dt):
    return (
        _d.datetime.strptime(str(day), "%Y%m%d")
        - _d.datetime.strptime(str(dt), "%Y%m%d")
    ).days


prev = run_day("20251009")
seen_ever = set(prev)
rows = []
for i, day in enumerate(cal):
    cur = run_day(day)
    fresh = [k for k in cur if k not in seen_ever]
    seen_ever |= set(cur)
    prev = cur
    nd = cal[i + 1] if i + 1 < len(cal) else None
    if not nd:
        continue
    for s, tp in fresh:
        if tp not in BUY_TYPES:
            continue
        dt = cur[(s, tp)]
        ac = adj_close[s]
        p_sig, p_ent = ac.get(str(dt)), ac.get(nd)
        if p_sig is None or p_ent is None or pd.isna(p_sig) or pd.isna(p_ent):
            continue
        ie = ac.index.get_loc(nd)
        j = min(ie + 20, len(ac.index) - 1)
        rows.append(
            {
                "stock": s,
                "type": tp,
                "conf_day": day,
                "lag": lag_days(day, dt),
                "runup": p_ent / p_sig - 1,
                "fwd20": float(ac.iloc[j] / p_ent - 1),
            }
        )
df = pd.DataFrame(rows)
df["bucket"] = pd.cut(
    df["runup"], [-1, 0.03, 0.08, 0.15, 99], labels=["<=3%", "3-8%", "8-15%", ">15%"]
)
print("确认日入场（次收）相对信号日涨幅分桶 × 入场后20日前瞻：")
g = df.groupby("bucket", observed=True)["fwd20"].agg(
    ["count", "mean", "median", lambda x: (x > 0).mean()]
)
g.columns = ["n", "mean", "median", "win"]
print(g.round(4).to_string())
print("\n按类型×桶：")
g2 = df.groupby(["type", "bucket"], observed=True)["fwd20"].agg(
    ["count", "mean", lambda x: (x > 0).mean()]
)
g2.columns = ["n", "mean", "win"]
print(g2.round(4).to_string())
print("\n滞后 vs runup 相关：", round(df["lag"].corr(df["runup"]), 3))
df.to_csv(
    os.path.join(BASE, "quant_backtest", "_probe4_runup_buckets.csv"),
    index=False,
    encoding="utf-8-sig",
)
print("csv: _probe4_runup_buckets.csv")
