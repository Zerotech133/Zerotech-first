# -*- coding: utf-8 -*-
"""LCS-1 信号层：tdx 数据 → 五算子假设（A6 第一小步）。

只做“数据适配 + 假设计算”，不做回测/收益；A6 回测另在后续 commit。
- tdx 分区 parquet：ts_daily + ts_adj_factor；
- 后复权：close/high/low × adj_factor；volume 取 vol；
- 复用 tools/chart_hypothesis.py 的五算子，避免另造口径。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import pyarrow.dataset as ds

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
import chart_hypothesis as CH  # noqa: E402

DEFAULT_DATA_ROOT = REPO / "quant_backtest/data/tdx"


def load_tdx_kline(code: str, data_root: Path | str = DEFAULT_DATA_ROOT, adjust: bool = True) -> pd.DataFrame:
    """从 tdx 分区表读取单票日线；adjust=True 时按 adj_factor 后复权。"""
    root = Path(data_root)
    daily_dir = root / "ts_daily"
    if not daily_dir.is_dir():
        raise FileNotFoundError("ts_daily missing: %s" % daily_dir)
    d = ds.dataset(str(daily_dir), format="parquet")
    t = d.to_table(filter=ds.field("ts_code") == code,
                   columns=["ts_code", "trade_date", "open", "high", "low", "close",
                            "pre_close", "pct_chg", "vol", "amount"])
    df = t.to_pandas()
    if df.empty:
        raise ValueError("no rows for %s" % code)
    df = df.sort_values("trade_date").reset_index(drop=True).rename(columns={"vol": "volume"})
    if adjust:
        adj_path = root / "ts_adj_factor"
        if adj_path.is_dir():
            a = ds.dataset(str(adj_path), format="parquet")
            at = a.to_table(filter=ds.field("ts_code") == code, columns=["ts_code", "trade_date", "adj_factor"])
            af = at.to_pandas()
            if not af.empty:
                df = df.merge(af[["trade_date", "adj_factor"]], on="trade_date", how="left")
                df["adj_factor"] = df["adj_factor"].ffill().fillna(1.0)
                for c in ("open", "high", "low", "close", "pre_close"):
                    df[c] = df[c] * df["adj_factor"]
    return df


def hypothesis_at(df: pd.DataFrame) -> dict:
    """对截至最后一行的 df 计算五算子 + 主假设；历史不足 250 行返回 unknown。"""
    if df is None or len(df) < 250:
        return {"ok": False, "error": "insufficient_history", "rows": 0 if df is None else len(df)}
    if "volume" not in df.columns and "vol" in df.columns:
        df = df.rename(columns={"vol": "volume"})
    ops = CH.operators(df)
    primary = CH.primary_hypothesis(ops)
    return {"ok": True, "rows": len(df), "ops": ops, "primary": primary}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="LCS-1 信号层（tdx→五算子）")
    ap.add_argument("--code", required=True)
    ap.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    ap.add_argument("--no-adjust", action="store_true")
    args = ap.parse_args(argv)
    df = load_tdx_kline(args.code, args.data_root, adjust=not args.no_adjust)
    out = hypothesis_at(df)
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
