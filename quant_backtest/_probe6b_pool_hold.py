# -*- coding: utf-8 -*-
"""K-STOCK-271 探针6b：窗口内池本身表现分解（100% 买入持有、逐股复利后平均）"""
import os
import json
import pandas as pd

BASE = r"C:/项目/老田的AI助手"
DATA = os.path.join(BASE, "quant_backtest", "data", "ashare")
POOL_SRC = os.path.join(BASE, "k_stock", "data", "poc_adapter_chanlun_20261012.json")
D0, D1 = "20251012", "20261012"

pool = json.load(open(POOL_SRC, encoding="utf-8"))
stocks = sorted({x["stock"] for x in pool})
terms = {}
for s in stocks:
    f = os.path.join(DATA, s.replace(".", "_") + ".parquet")
    if not os.path.exists(f):
        continue
    b = pd.read_parquet(f).sort_values("trade_date")
    seg = b[(b.trade_date >= D0) & (b.trade_date <= D1)]
    if len(seg) < 100:      # 窗口内基本全程有数据
        continue
    terms[s] = (1 + seg["pct_chg"] / 100.0).prod() - 1

t = pd.Series(terms).sort_values()
n = len(t)
print(f"窗口 {D0}->{D1} 全程在池股票数: {n}")
print(f"买入持有100%(初始等权,零成本): 总收益 {t.mean()*100:.2f}%  中位数 {t.median()*100:.2f}%")
print(f"上涨家数: {(t>0).sum()} / 下跌家数: {(t<=0).sum()}")
print("最差5只:")
for s, v in t.head(5).items():
    print(f"  {s} {v*100:.1f}%")
print("最好5只:")
for s, v in t.tail(5).items():
    print(f"  {s} {v*100:.1f}%")
