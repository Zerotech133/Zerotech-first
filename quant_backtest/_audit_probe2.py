# -*- coding: utf-8 -*-
"""K-STOCK-273 探针b：数据源体检疑点追查"""
import os, json, glob
import pandas as pd

BASE = r"C:/项目/老田的AI助手/quant_backtest/data"

# 1) schema 疑点：抽 5 个文件比列集合（不比顺序）
print("== 1) schema 列集合对比（忽略列序） ==")
base_cols = None
diffs = []
files = sorted(glob.glob(os.path.join(BASE, "ashare", "*.parquet")))
for f in files[::800] + [files[-1]]:
    b = pd.read_parquet(f)
    cs = set(b.columns)
    if base_cols is None:
        base_cols = cs
        print(os.path.basename(f), sorted(cs))
    elif cs != base_cols:
        diffs.append((os.path.basename(f), sorted(cs)))
print("列集合不同的文件:", diffs if diffs else "无 —— 之前报的 5592 异常只是【列顺序】差异，非数据问题")

# 2) 非正价格文件点名
print("\n== 2) 非正价格文件点名 ==")
for f in files:
    b = pd.read_parquet(f, columns=["open", "high", "low", "close"])
    if not ((b > 0).all().all()):
        s = os.path.basename(f)
        bad = b[(b[["open", "high", "low", "close"]] <= 0).any(axis=1)]
        print(s, "坏行数", len(bad))
        print(bad.head(5).to_string())

# 3) 复权因子湖真实覆盖（按文件名分区则直接看文件名两端）
print("\n== 3) 复权因子湖真实覆盖 ==")
for tag, d in (("tushare/adj_factor", os.path.join(BASE, "tushare", "adj_factor")),
               ("tdx/ts_adj_factor", os.path.join(BASE, "tdx", "ts_adj_factor"))):
    fs = sorted(glob.glob(os.path.join(d, "*")))
    names = [os.path.basename(x) for x in fs]
    print(tag, "文件数", len(fs), "首3:", names[:3], "末3:", names[-3:])
    # 抽首、中、尾各2个读范围
    for f in [fs[0], fs[len(fs)//2], fs[-1]]:
        try:
            a = pd.read_parquet(f)
            c = [x for x in a.columns if "trade_date" in x]
            dd = a[c[0]].astype(str)
            print("  ", os.path.basename(f), "rows", len(a), "range", dd.min(), "->", dd.max())
        except Exception as e:
            print("  ", os.path.basename(f), "ERR", str(e)[:60])

# 4) 35 个陈旧文件分类（退市 vs 停牌 vs 其他）
print("\n== 4) 陈旧文件分类（最新日 != 20261012） ==")
stale = []
for f in files:
    b = pd.read_parquet(f, columns=["trade_date"])
    hi = b["trade_date"].astype(str).max()
    if hi != "20261012":
        stale.append((os.path.basename(f).replace(".parquet", ""), hi, len(b)))
for s, hi, n in sorted(stale, key=lambda x: x[1]):
    print(f"  {s}  最后日={hi}  行数={n}")

# 5) 大空洞文件抽查一格（看是停牌还是缺数）
print("\n== 5) 大空洞抽查 002731_SZ（断档68日） ==")
b = pd.read_parquet(os.path.join(BASE, "ashare", "002731_SZ.parquet")).sort_values("trade_date")
seg = b[b.trade_date >= "20251012"]
dt = pd.to_datetime(seg["trade_date"], format="%Y%m%d").diff().dt.days
i = dt.idxmax()
print("断档前最后日:", seg.loc[i-1, "trade_date"], "恢复日:", seg.loc[i, "trade_date"], "空", dt.max(), "天")
r = b[(b.trade_date >= seg.loc[i-1, "trade_date"]) & (b.trade_date <= seg.loc[i, "trade_date"])]
print(r[["trade_date", "close", "volume", "amount", "pct_chg"]].to_string())

# 6) 000001_SH 是不是指数混入股票库
print("\n== 6) 000001_SH 身份 ==")
b = pd.read_parquet(os.path.join(BASE, "ashare", "000001_SH.parquet"))
print(b.head(3).to_string(), "\n行数", len(b), "范围", b.trade_date.min(), "->", b.trade_date.max())
