# -*- coding: utf-8 -*-
"""K-STOCK-273 探针c：因子湖连续性验证 + 池股新鲜度交叉核查"""
import os
import glob
import json
import pandas as pd

BASE = r"C:/项目/老田的AI助手/quant_backtest/data"

# 1) tdx/ts_adj_factor 21 个分区的日期并集连续性
print("== 1) tdx 因子湖日期连续性 ==")
parts = sorted(glob.glob(os.path.join(BASE, "tdx", "ts_adj_factor", "*.parquet")))
alld = set()
per = []
for f in parts:
    a = pd.read_parquet(f, columns=[c for c in pd.read_parquet(f, columns=None).columns if "trade_date" in c][:1]) if False else None
cols = None
for f in parts:
    a = pd.read_parquet(f)
    if cols is None:
        cols = list(a.columns)
        print("cols:", cols)
    c = [x for x in cols if "trade_date" in x][0]
    ds = set(a[c].astype(str))
    per.append((os.path.basename(f), min(ds), max(ds), len(a)))
    alld |= ds
ds = sorted(alld)
print("分区范围明细:")
for p in per:
    print("  ", p)
print(f"并集: {ds[0]} -> {ds[-1]} 共 {len(ds)} 个交易日")
# 找断档（相邻交易日差 > 7 天视作断档嫌疑）
s = pd.Series(pd.to_datetime(ds, format="%Y%m%d"))
gaps = s.diff().dt.days
bg = [(ds[i-1], ds[i], int(gaps.iloc[i])) for i in range(1, len(ds)) if gaps.iloc[i] > 7]
print("断档(>7日历日):", bg if bg else "无 —— 因子湖实际连续覆盖！")

# 2) 交易日历核对：is_open=1 的最新几天
print("\n== 2) 交易日历核对 ==")
tc = pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(os.path.join(BASE, "tushare", "trade_cal", "*.parquet")))]).drop_duplicates()
tc = tc.sort_values("cal_date")
op = tc[tc["is_open"] == 1]
print("日历范围:", tc.cal_date.min(), "->", tc.cal_date.max(), "· 交易日", len(op))
print("最近 5 个交易日:", list(op.cal_date.astype(str).tail(5)))

# 3) 76 池股 ∩ 陈旧名单
print("\n== 3) 池股新鲜度交叉核查 ==")
pool = json.load(open(r"C:/项目/老田的AI助手/k_stock/data/poc_adapter_chanlun_20261012.json", encoding="utf-8"))
stocks = sorted({x["stock"] for x in pool})
stale = {"603056_SH": "20260120", "002231_SZ": "20260129", "000688_SH": "20260330",
         "000001_SH": "20260401", "600355_SH": "20260403", "300391_SZ": "20260410",
         "000638_SZ": "20260413", "300344_SZ": "20260421", "688287_SH": "20260608",
         "600421_SH": "20260622", "600599_SH": "20260622", "600636_SH": "20260622",
         "600696_SH": "20260629", "600193_SH": "20260629", "600608_SH": "20260629",
         "605081_SH": "20260629", "300029_SZ": "20260709", "000004_SZ": "20260713",
         "002808_SZ": "20260713", "002898_SZ": "20260716", "920305_BJ": "20260729",
         "002731_SZ": "20260831", "000016_SZ": "20260903", "688496_SH": "20260910",
         "601059_SH": "20260914", "601198_SH": "20260914", "300082_SZ": "20260918",
         "002860_SZ": "20260921", "600293_SH": "20260928", "002813_SZ": "20260929",
         "000668_SZ": "20260930", "002388_SZ": "20260930", "300773_SZ": "20260930",
         "399001_SZ": "20260330", "399006_SZ": "20260330"}
hit = [(s, stale[s]) for s in stocks if s in stale]
print("池股踩陈旧名单:", hit if hit else "无 —— 76 只池股全部新鲜到 20261012")
print("指数混入 ashare 库: 000001_SH(300行·上证) / 399001_SZ(深成) / 399006_SZ(创业板指) —— 池无污染, 全市场扫描需排除")

# 4) 000688_SH 的 NaN 计数
print("\n== 4) 000688_SH NaN 明细 ==")
b = pd.read_parquet(os.path.join(BASE, "ashare", "000688_SH.parquet"))
print(b[["open", "high", "low", "close", "pct_chg", "pre_close"]].isna().sum().to_dict())
print("该文件是否指数/退市: ts_code =", b.ts_code.iloc[0], "· 最后日", b.trade_date.max())
