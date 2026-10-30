# -*- coding: utf-8 -*-
"""_bench_duck.py -- DuckDB 湖查询层 vs pandas 直接读取的耗时对照（同机同数据）。

测法：
- DuckDB 路径：duck_lake.DuckLake 建视图后执行 SQL，计 wall time。
- pandas 路径：pyarrow.dataset 枚举与 DuckDB 视图完全相同的 parquet 文件集合，
  read 成 pandas DataFrame 后用等价 pandas 语义做同样的聚合，计 wall time
  （含读取+计算，DuckDB 路径同样含视图解析+扫描+计算，口径对齐）。
- 每个查询跑 2 遍取最优（缓解页缓存随机性），并记录首遍（冷）耗时。
"""

from __future__ import annotations

import sys
import time

sys.path.insert(0, r"C:\aios\quant_backtest")

import duck_lake  # noqa: E402

LAKE = duck_lake.DuckLake(verbose=False)
LAKE.build()
CON = LAKE.con

import pyarrow.dataset as ds  # noqa: E402
import pandas as pd  # noqa: E402

TS_DAILY_GLOB = LAKE.views["ts_daily"].glob
ADJ_GLOB = LAKE.views["adj_factor"].glob


def glob_to_files(glob_expr: str) -> list:
    """把 duck_lake 推导的 glob 展开为确定性的文件列表（pandas 基线用同一集合）。"""
    import glob as g

    py_expr = glob_expr.replace("[!_]*", "*")
    if "**" in py_expr:
        files = [
            f
            for f in g.glob(py_expr, recursive=True)
            if not f.replace("\\", "/").rsplit("/", 1)[1].startswith("_")
        ]
    else:
        files = [
            f
            for f in g.glob(py_expr)
            if not f.replace("\\", "/").rsplit("/", 1)[1].startswith("_")
        ]
    return sorted(files)


def timed(label: str, fn, runs: int = 2) -> dict:
    """跑 runs 遍，打印每遍耗时，返回 best。"""
    results = []
    for i in range(runs):
        t0 = time.perf_counter()
        out = fn()
        dt = time.perf_counter() - t0
        results.append(dt)
        print(f"  {label} run{i + 1}: {dt:.3f}s  -> {out}")
    return {"label": label, "runs": results, "best": min(results)}


# --------------------------------------------------------------------------
# Q1: ts_daily 全市场某日涨幅分布（46 分片全扫，过滤单日）
# --------------------------------------------------------------------------
def q1_duck() -> str:
    rows = CON.execute("""
        SELECT CASE
                 WHEN pct_chg >=  9.9 THEN 'A_>=9.9'
                 WHEN pct_chg >=  5   THEN 'B_5~9.9'
                 WHEN pct_chg >=  0   THEN 'C_0~5'
                 WHEN pct_chg >= -5   THEN 'D_-5~0'
                 WHEN pct_chg >  -9.9 THEN 'E_-9.9~-5'
                 ELSE 'F_<=-9.9'
               END AS bucket,
               count(*) AS n
        FROM ts_daily
        WHERE trade_date = '20261014'
        GROUP BY 1 ORDER BY 1
    """).fetchall()
    return f"{len(rows)} buckets, total={sum(r[1] for r in rows)}"


def q1_pandas() -> str:
    files = glob_to_files(TS_DAILY_GLOB)
    tbl = ds.dataset(files, format="parquet").to_table(
        columns={"trade_date": ds.field("trade_date"), "pct_chg": ds.field("pct_chg")}
    )
    df = tbl.to_pandas()
    day = df[df["trade_date"] == "20261014"]
    bins = [-float("inf"), -9.9, -5, 0, 5, 9.9, float("inf")]
    labels = ["F_<=-9.9", "E_-9.9~-5", "D_-5~0", "C_0~5", "B_5~9.9", "A_>=9.9"]
    buckets = pd.cut(day["pct_chg"], bins=bins, labels=labels).value_counts()
    return f"{len(buckets)} buckets, total={int(buckets.sum())}"


# --------------------------------------------------------------------------
# Q2: ts_daily 按月行数统计（46 分片全量聚合）
# --------------------------------------------------------------------------
def q2_duck() -> str:
    rows = CON.execute("""
        SELECT substr(trade_date, 1, 6) AS ym, count(*) AS n
        FROM ts_daily GROUP BY 1 ORDER BY 1
    """).fetchall()
    return f"{len(rows)} months, rows={sum(r[1] for r in rows)}"


def q2_pandas() -> str:
    files = glob_to_files(TS_DAILY_GLOB)
    tbl = ds.dataset(files, format="parquet").to_table(
        columns={"trade_date": ds.field("trade_date")}
    )
    df = tbl.to_pandas()
    months = df.groupby(df["trade_date"].str[:6]).size()
    return f"{len(months)} months, rows={int(months.sum())}"


# --------------------------------------------------------------------------
# Q3: adj_factor（tushare by_date，7957 个日文件）按年行数统计
# --------------------------------------------------------------------------
def q3_duck() -> str:
    rows = CON.execute("""
        SELECT substr(trade_date, 1, 4) AS y, count(*) AS n
        FROM adj_factor GROUP BY 1 ORDER BY 1
    """).fetchall()
    return f"{len(rows)} years, rows={sum(r[1] for r in rows)}"


def q3_pandas() -> str:
    files = glob_to_files(ADJ_GLOB)
    tbl = ds.dataset(files, format="parquet").to_table(
        columns={"trade_date": ds.field("trade_date")}
    )
    df = tbl.to_pandas()
    years = df.groupby(df["trade_date"].str[:4]).size()
    return f"{len(years)} years, rows={int(years.sum())}"


# --------------------------------------------------------------------------
# Q4: etf_mins（tushare by_code_month 分片）某月行情聚合
# --------------------------------------------------------------------------
# etf_mins 的时间列名先探测（分钟表可能是 trade_time）
etf_cols = [d[0] for d in CON.execute("DESCRIBE etf_mins").fetchall()]
ETF_TCOL = next(c for c in etf_cols if "date" in c or "time" in c)


def q4_duck() -> str:
    rows = CON.execute(
        """
        SELECT ts_code, count(*) AS bars, max(close) AS max_close
        FROM etf_mins
        WHERE substr(CAST(__ETF_TCOL__ AS VARCHAR), 1, 7) = '2026-10'
        GROUP BY 1 ORDER BY 1 LIMIT 1000
    """.replace("__ETF_TCOL__", ETF_TCOL)).fetchall()
    return f"{len(rows)} codes, bars={sum(r[1] for r in rows)}"


def q4_pandas() -> str:
    import glob as g

    files = sorted(
        f
        for f in g.glob(
            r"C:\aios\quant_backtest\data\tushare\etf_mins\*_202610.parquet"
        )
    )
    tbl = ds.dataset(files, format="parquet").to_table()
    df = tbl.to_pandas()
    col = ETF_TCOL
    key = df[col].astype(str).str[:7]
    sel = df[key == "2026-10"]
    agg = sel.groupby("ts_code").agg(bars=(col, "size"), max_close=("close", "max"))
    return f"{len(agg)} codes, bars={int(agg['bars'].sum())}"


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    n_files_ts = len(glob_to_files(TS_DAILY_GLOB))
    n_files_adj = len(glob_to_files(ADJ_GLOB))
    print(f"ts_daily 文件数: {n_files_ts} | adj_factor 文件数: {n_files_adj}")
    print(f"etf_mins 时间列: {ETF_TCOL} | 列: {etf_cols}")
    print()

    for name, duck_fn, pd_fn in [
        ("Q1 ts_daily 某日涨幅分布", q1_duck, q1_pandas),
        ("Q2 ts_daily 按月行数统计", q2_duck, q2_pandas),
        ("Q3 adj_factor 按年行数统计", q3_duck, q3_pandas),
        ("Q4 etf_mins 202610 聚合", q4_duck, q4_pandas),
    ]:
        print(f"== {name} ==")
        d = timed("DuckDB ", duck_fn)
        p = timed("pandas ", pd_fn)
        speedup = p["best"] / d["best"] if d["best"] > 0 else float("inf")
        print(
            f"  >>> best: DuckDB {d['best']:.3f}s vs pandas {p['best']:.3f}s"
            f"  (DuckDB 快 {speedup:.1f}x)"
        )
        print()
