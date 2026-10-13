# -*- coding: utf-8 -*-
"""323 探针：ts_rt_min 的 vol/amount 口径（累计 vs 增量）+ 14:45 完备性。"""
import glob

import pandas as pd

# 1) 单票单日全天时序：看 vol/amount 是否单调递增（累计口径）
fs = sorted(glob.glob("E:/老田的AI助手/quant_backtest/data/tdx/ts_rt_min/*.parquet"))
found = None
for fp in fs:
    d = pd.read_parquet(fp, columns=["ts_code", "time", "open", "high", "low", "close", "vol", "amount"])
    sub = d[(d.ts_code == "000001.SZ") & (d.time.astype(str).str.startswith("2026-08-06"))]
    if len(sub):
        found = (fp, sub.sort_values("time"))
        break
if found is None:
    print("000001.SZ 2026-08-06 不在首几片，需按日期定位 part")
else:
    fp, sub = found
    print("part:", fp.split("\\")[-1], "| rows:", len(sub))
    print(sub.head(3).to_string())
    print("...")
    print(sub.tail(3).to_string())
    print("vol 单调递增:", bool(sub.vol.is_monotonic_increasing),
          "| amount 单调递增:", bool(sub.amount.is_monotonic_increasing))

# 2) 14:45 时刻行覆盖：随机抽 3 个 part 数 14:45:00 行占比
for fp in fs[:1] + fs[60:61] + fs[-1:]:
    d = pd.read_parquet(fp, columns=["ts_code", "time"])
    t = d.time.astype(str)
    n45 = (t.str.contains(" 14:45:00")).sum()
    print(fp.split("\\")[-1], "rows:", len(d), "| 14:45:00 行:", n45,
          "| distinct days:", t.str[:10].nunique())
