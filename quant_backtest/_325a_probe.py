# -*- coding: utf-8 -*-
"""K-STOCK-325-A：stk_mins 覆盖缺口与量纲勘查。

1) 890 只票与市场宇宙（market_scan，剔 ST/BJ/指数）交集与缺口；
2) 202610 新鲜度（最新 trade_time 是否到 2026-10-12）；
3) vol/amount 量纲：tushare 1min 当日累计 vs ts_daily 全天额对齐倍数；
4) 回补量级估算（缺口票 × 月份 × 单次调用耗时）。
"""

import glob
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# ── 1) 宇宙与已有覆盖 ────────────────────────────────────────
import market_scan  # noqa: E402

uni = market_scan.market_stocks()
print("market universe:", len(uni), "只")

have = set()
for fp in glob.glob(os.path.join(HERE, "data", "tushare", "stk_mins", "*.parquet")):
    b = os.path.basename(fp)  # 000001_SZ_202509.parquet
    code, ym = b.rsplit("_", 1)[0], b.rsplit("_", 1)[1][:6]
    if ym >= "202607":
        have.add(code.replace("_", "."))
have_n = len(have)
print("stk_mins 已覆盖(≥202607):", have_n, "只")

uni_set = set(uni)
gap = sorted(uni_set - have)
print("缺口票:", len(gap), "只 | 样本:", gap[:5])

# 已覆盖票是否在宇宙内（若大量在宇宙外说明代码风格或来源不同）
print("已覆盖票在宇宙内:", len(have & uni_set))

# ── 2) 202610 新鲜度 ────────────────────────────────────────
d = pd.read_parquet(
    os.path.join(HERE, "data", "tushare", "stk_mins", "000001_SZ_202610.parquet")
)
d["trade_time"] = pd.to_datetime(d["trade_time"])
print(
    "\n000001.SZ 202610: 最早",
    d.trade_time.min(),
    "最新",
    d.trade_time.max(),
    "| rows",
    len(d),
)
print("最新交易日:", d.trade_time.dt.strftime("%Y%m%d").max())

# ── 3) 量纲校准：tushare 1min 当日累计 vs ts_daily ──────────
day = "20260130"
m = d[d.trade_time.dt.strftime("%Y%m%d") == day].sort_values("trade_time")
vol_cum = m.vol.sum()
amt_cum = m.amount.sum()
td = pd.read_parquet(
    os.path.join(HERE, "data", "tdx", "ts_daily"),
    columns=["ts_code", "trade_date", "vol", "amount"],
)
r = td[(td.ts_code == "000001.SZ") & (td.trade_date.astype(str) == day)]
if len(r):
    tvol, tamt = float(r.vol.iloc[0]), float(r.amount.iloc[0])
    print(
        "\n%s 当日累计: tushare vol=%.0f amount=%.0f | ts_daily vol=%.0f amount=%.0f"
        % (day, vol_cum, amt_cum, tvol, tamt)
    )
    print(
        "倍数: vol ts_daily/tushare=%.3f | amount=%.3f"
        % (tvol / vol_cum if vol_cum else 0, tamt / amt_cum if amt_cum else 0)
    )
else:
    print("ts_daily 无", day, "对照行")

# ── 4) 回补量级估算 ─────────────────────────────────────────
months = ["202607", "202608", "202609", "202610"]
n_calls = len(gap) * len(months)
print("\n回补量级: %d 票 × %d 月 = %d 次调用" % (len(gap), len(months), n_calls))
print("按 0.5s/次（SLEEP 0.35 + 开销）≈ %.1f h；限频退避另计" % (n_calls * 0.5 / 3600))
