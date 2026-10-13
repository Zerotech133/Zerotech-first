# -*- coding: utf-8 -*-
"""325-C 前置：tushare 1min 与 ts_rt_min 同日同票逐分钟比对（戳法/量纲交叉验证）。"""
import glob
import os

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
code = "000001.SZ"
day = "20260831"  # 重叠窗口内的一天

# ts_rt_min 侧
cand = [fp for fp in glob.glob(os.path.join(HERE, "data", "tdx", "ts_rt_min", "*.parquet"))]
hit = None
for fp in cand:
    d = pd.read_parquet(fp, columns=["ts_code", "time"])
    d["time"] = d["time"].astype(str)
    if ((d.ts_code == code) & d.time.str.startswith("2026-08-31")).any():
        hit = fp
        break
print("ts_rt_min part:", hit)
a = pd.read_parquet(hit)
a["time"] = a["time"].astype(str)
a = a[(a.ts_code == code) & a.time.str.startswith("2026-08-31")].sort_values("time")
print("ts_rt_min 行数:", len(a), "| 首:", a.time.iloc[0], "| 末:", a.time.iloc[-1])

# tushare 侧
b = pd.read_parquet(os.path.join(HERE, "data", "tushare", "stk_mins", "000001_SZ_202608.parquet"))
b["trade_time"] = b["trade_time"].astype(str)
b = b[(b.ts_code == code) & b.trade_time.str.startswith("2026-08-31")].sort_values("trade_time")
print("tushare 行数:", len(b), "| 首:", b.trade_time.iloc[0], "| 末:", b.trade_time.iloc[-1])

# 逐分钟 close 比对（14:30-14:50 区间）
a2 = a[a.time.str[11:16].between("14:30", "14:50")][["time", "close", "vol", "amount"]].copy()
a2["mm"] = a2.time.str[11:16]
b2 = b[b.trade_time.str[11:16].between("14:30", "14:50")][["trade_time", "close", "vol", "amount"]].copy()
b2["mm"] = b2.trade_time.str[11:16]
j = a2.merge(b2, on="mm", suffixes=("_a", "_b"))
print("\n对齐分钟数:", len(j), "/ 21")
j["close_diff"] = j.close_a - j.close_b
print("close 差异非零行:", int((j.close_diff.abs() > 1e-9).sum()))
print(j[["mm", "close_a", "close_b", "vol_a", "vol_b", "amount_a", "amount_b"]].to_string(index=False))
