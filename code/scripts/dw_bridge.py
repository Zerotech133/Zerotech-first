# -*- coding: utf-8 -*-
"""张松数据仓只读桥接：日线/ST/list_date 查询，不写库。

用法：
  python scripts/dw_bridge.py --code 000301.SZ
  python scripts/dw_bridge.py --st 2026-10-30
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pyarrow.dataset as ds

DW = Path(r"C:\项目\老田的AI助手\quant_backtest\data")
DAILY = DW / "tdx" / "ts_daily"
NAMECHANGE = DW / "tdx" / "ts_namechange"
BASIC = DW / "tdx" / "ts_stock_basic.parquet"


def daily_for(code: str, limit: int = 5) -> list[dict]:
    if not DAILY.exists():
        return []
    dataset = ds.dataset(str(DAILY), format="parquet")
    table = dataset.to_table(
        columns=["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "pct_chg", "vol", "amount"],
        filter=ds.field("ts_code") == code,
    )
    df = table.to_pandas().sort_values("trade_date").tail(limit)
    return json.loads(df.to_json(orient="records", date_format="iso"))


def st_codes(day: str) -> list[str]:
    if not NAMECHANGE.exists():
        return []
    dataset = ds.dataset(str(NAMECHANGE), format="parquet")
    table = dataset.to_table(columns=["ts_code", "name", "start_date", "end_date"])
    df = table.to_pandas()
    day_i = int(day.replace("-", ""))
    start = df["start_date"].fillna(0).astype(int)
    end = df["end_date"].fillna(99999999).astype(int)
    hit = df[(start <= day_i) & (end >= day_i)]
    return sorted(set(hit["ts_code"].tolist()))


def basic_for(code: str) -> dict:
    if not BASIC.exists():
        return {}
    import pandas as pd
    df = pd.read_parquet(BASIC)
    row = df[df["ts_code"] == code]
    if row.empty:
        return {}
    return json.loads(row.iloc[0].to_json())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code")
    ap.add_argument("--st")
    ap.add_argument("--limit", type=int, default=5)
    args = ap.parse_args()
    out = {}
    if args.code:
        out["code"] = args.code
        out["daily"] = daily_for(args.code, args.limit)
        out["basic"] = basic_for(args.code)
    if args.st:
        out["st_date"] = args.st
        out["st_codes"] = st_codes(args.st)
        out["st_count"] = len(out["st_codes"])
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
