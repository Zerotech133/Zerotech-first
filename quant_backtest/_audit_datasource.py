# -*- coding: utf-8 -*-
"""
K-STOCK-273 · 数据源全面体检（老田 10-13 06:46 指令「检查我们的数据源」）
覆盖回测链四个直接数据源：
  S1 data/ashare/*.parquet       日线主库（全量扫描）
  S2 data/tushare/adj_factor/    复权因子（tushare）
  S3 data/tdx/ts_adj_factor/     复权因子（tdx 补充）
  S4 data/tushare/trade_cal      交易日历
  S5 data/tushare/daily + index_daily 抽查对照
输出：stdout 摘要 + data/integrity/audit_20261013.json
"""
import os, json, glob
import pandas as pd
import numpy as np

BASE = r"E:/老田的AI助手/quant_backtest/data"
TODAY = "20261012"          # 数据应到的最后交易日（10-13 为周二盘前，数据只到 10-12）
REPORT = {}

def sec(title):
    print("\n" + "=" * 8 + " " + title + " " + "=" * 8, flush=True)

# ---------- S1: ashare 全量扫描 ----------
sec("S1 ashare 日线主库")
files = sorted(glob.glob(os.path.join(BASE, "ashare", "*.parquet")))
n = len(files)
schema_cols, latest_dates, min_dates, rows_total = None, [], [], 0
stale, short_hist, bad_cols, dup_dates, gap_big, exdiv_susp = [], [], [], [], [], []
nan_amount_rate_high, neg_price, pct_close_conflict = 0, 0, 0
WARM = 250
for i, f in enumerate(files):
    try:
        b = pd.read_parquet(f)
    except Exception as e:
        bad_cols.append((os.path.basename(f), "READ_FAIL:" + str(e)[:50])); continue
    if schema_cols is None:
        schema_cols = list(b.columns)
        print("schema:", schema_cols, flush=True)
    else:
        if list(b.columns) != schema_cols:
            bad_cols.append((os.path.basename(f), "COLS_DIFF"))
    s = os.path.basename(f).replace(".parquet", "")
    ds = b["trade_date"].astype(str)
    if ds.duplicated().any():
        dup_dates.append(s)
    lo, hi = ds.min(), ds.max()
    latest_dates.append(hi); min_dates.append(lo)
    rows_total += len(b)
    if hi != TODAY:
        stale.append((s, hi, len(b)))
    if len(b) < 600:                      # ~2.5 年不到 → 短历史
        short_hist.append((s, lo, hi, len(b)))
    if not ((b[["open", "high", "low", "close"]] > 0).all().all()):
        neg_price += 1
    # 除权假跳空探测（pct_chg vs close 变动）——只统计不修
    cc = b["close"].pct_change() * 100
    d = (b["pct_chg"] - cc).abs()
    k = int((d > 0.5).sum())
    if k:
        exdiv_susp.append((s, k))
    # 一年以上窗口内的大空洞
    if hi >= "20251012":
        seg = b[b.trade_date >= "20251012"].sort_values("trade_date")
        dt = pd.to_datetime(seg["trade_date"], format="%Y%m%d").diff().dt.days
        if (dt.iloc[1:] > 30).any() if len(dt) > 1 else False:
            gap_big.append((s, int(dt.max())))
print(f"文件数 {n} · 总行数 {rows_total:,} · schema 一致性异常 {len(bad_cols)}")
lds = pd.Series(latest_dates)
print(f"最新日期分布: {lds.value_counts().head(3).to_dict()}")
print(f"未到 {TODAY} 的陈旧文件: {len(stale)}")
if stale[:8]:
    for x in stale[:8]: print("  stale:", x)
print(f"短历史(<600行): {len(short_hist)}")
for x in short_hist[:8]: print("  short:", x)
print(f"重复交易日文件: {len(dup_dates)}  {dup_dates[:5]}")
print(f"大空洞(窗口内断档>30日): {len(gap_big)}  {gap_big[:5]}")
print(f"含非正价格文件: {neg_price}")
print(f"pct_chg vs close 差异>0.5%的文件数(除权痕迹): {len(exdiv_susp)} (合计 {sum(k for _,k in exdiv_susp)} 根)")
w = sorted(exdiv_susp, key=lambda x: -x[1])[:5]
print("  TOP:", w)

# ---------- S2/S3: 复权因子 ----------
sec("S2/S3 复权因子湖")
for tag, d in (("tushare/adj_factor", os.path.join(BASE, "tushare", "adj_factor")),
               ("tdx/ts_adj_factor", os.path.join(BASE, "tdx", "ts_adj_factor"))):
    fs = sorted(glob.glob(os.path.join(d, "*.parquet"))) or sorted(glob.glob(os.path.join(d, "*.csv")))
    if not fs:
        print(f"{tag}: 目录空或无文件!"); continue
    los, his = [], []
    for f in fs[:400]:
        try:
            a = pd.read_parquet(f) if f.endswith(".parquet") else pd.read_csv(f)
            c = [x for x in a.columns if "trade_date" in x or x == "date"]
            if not c: continue
            dd = a[c[0]].astype(str)
            los.append(dd.min()); his.append(dd.max())
        except Exception:
            pass
    print(f"{tag}: 文件 {len(fs)} 个（抽样 {len(los)}）· 范围 {min(los)} → {max(his)}")

# ---------- S4: 交易日历 ----------
sec("S4 交易日历 trade_cal")
tc_f = os.path.join(BASE, "tushare", "trade_cal")
if os.path.isdir(tc_f):
    fs2 = sorted(glob.glob(os.path.join(tc_f, "*")))
    print("内容:", [os.path.basename(x) for x in fs2][:10])
    for f in fs2[:3]:
        try:
            a = pd.read_parquet(f) if f.endswith(".parquet") else pd.read_csv(f)
            print(os.path.basename(f), "cols:", list(a.columns)[:6], "rows:", len(a))
        except Exception as e:
            print(os.path.basename(f), "ERR", str(e)[:60])
elif os.path.isfile(tc_f + ".parquet") or os.path.isfile(tc_f + ".csv"):
    print("single file", tc_f)
else:
    print("trade_cal 缺失？搜索中…")
    g = glob.glob(os.path.join(BASE, "tushare", "*trade*"))
    print(g)

# ---------- S5: tushare daily / index_daily 抽查对照 ----------
sec("S5 tushare daily 与 index_daily 抽查")
dd = os.path.join(BASE, "tushare", "daily")
if os.path.isdir(dd):
    fs3 = sorted(glob.glob(os.path.join(dd, "*")))
    print("daily 文件形态:", len(fs3), [os.path.basename(x) for x in fs3[:3]])
idx = os.path.join(BASE, "index_daily")
if os.path.isdir(idx):
    fs4 = sorted(glob.glob(os.path.join(idx, "*")))
    print("index_daily:", [os.path.basename(x) for x in fs4][:15])
    for f in fs4[:3]:
        try:
            a = pd.read_parquet(f)
            c = [x for x in a.columns if "date" in x or "trade" in x]
            print(" ", os.path.basename(f), "cols:", list(a.columns)[:8], "range:", str(a[c[0]].min())[:10], "->", str(a[c[0]].max())[:10])
        except Exception as e:
            print(" ", os.path.basename(f), "ERR", str(e)[:60])

# ---------- 归档 ----------
REPORT = {"files": n, "rows": rows_total, "stale": len(stale), "stale_top": stale[:10],
          "short_hist": len(short_hist), "dup_dates": dup_dates, "gap_big": gap_big,
          "bad_cols": [list(x) for x in bad_cols[:20]], "exdiv_files": len(exdiv_susp)}
os.makedirs(os.path.join(BASE, "integrity"), exist_ok=True)
json.dump(REPORT, open(os.path.join(BASE, "integrity", "audit_20261013.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("\nJSON -> data/integrity/audit_20261013.json")
