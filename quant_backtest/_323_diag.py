# -*- coding: utf-8 -*-
"""323 诊断：0 信号归因——quant_double_knife 各条件在 18 日快照上的失活分布。"""
import sys

import pandas as pd
import numpy as np

sys.path.insert(0, r"C:\项目\老田的AI助手")
from agent_platform.engine.quant_double_knife import quant_double_knife

snap = pd.read_parquet(r"C:\项目\老田的AI助手\quant_backtest\data\tdx\dknife_snap\dknife_snap.parquet")
b = snap[snap.ts_code == "000001.SZ"].sort_values("trade_date")
df = pd.DataFrame({"open": b.open.values, "high": b.high.values,
                   "low": b.low.values, "close": b.close.values, "amount": b.amount.values})
out = quant_double_knife(df, profile="relaxed")
print("行数:", len(df), "| signal 真值数:", int(out.double_knife_signal.fillna(False).sum()))
# 复算各条件（relaxed 配置）
close, open_ = df.close, df.open
high, low = df.high, df.low
amount = df.amount
range_ = (high - low).abs()
body = (close - open_).abs()
cross_ok = body <= range_ * 0.35          # 十字星（relaxed 35%？以引擎实现为准，此处示意）
pct1 = close.pct_change().fillna(0.0)
pct2 = close.shift(1).pct_change().fillna(0.0)
macd_like = (pct1 > 0) & (pct2 > 0)       # 近似多头（示意）
amount_20d = amount.rolling(20, min_periods=20).sum()
print("amount_20d 非空数:", int(amount_20d.notna().sum()), "/ %d（18 行 < 20 日滚动窗）" % len(df))
# 直接看引擎输出的中间列名
print("引擎输出列:", [c for c in out.columns if c != "double_knife_signal"][:20])
for c in out.columns:
    if out[c].dtype == bool or out[c].dropna().isin([True, False]).all():
        print("  %-28s True=%d" % (c, int(out[c].fillna(False).sum())))
