# -*- coding: utf-8 -*-
"""LCS-1 信号层单测（A6 第一小步）。"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

TOOL = Path(__file__).resolve().parents[1]
REPO = TOOL.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(TOOL))
import lcs1_signal as S  # noqa: E402


def _synthetic(n=300, start_price=10.0):
    dates = pd.bdate_range("2024-01-01", periods=n).strftime("%Y%m%d").astype(int)
    close = start_price + np.arange(n) * 0.01
    return pd.DataFrame({
        "ts_code": "600337.SH",
        "trade_date": dates,
        "open": close * 0.995,
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "pre_close": np.r_[close[0], close[:-1]],
        "pct_chg": np.r_[0.0, np.diff(close) / close[:-1] * 100],
        "vol": 1_000_000.0,
        "amount": 1e7,
    })


def _write_root(tmp_path, df):
    root = tmp_path / "tdx"
    (root / "ts_daily").mkdir(parents=True)
    (root / "ts_adj_factor").mkdir()
    pq.write_table(pa.Table.from_pandas(df), root / "ts_daily" / "part_0000.parquet")
    adj = df[["ts_code", "trade_date"]].copy()
    adj["adj_factor"] = 2.0
    pq.write_table(pa.Table.from_pandas(adj), root / "ts_adj_factor" / "part_0000.parquet")
    return root


def test_load_tdx_adjusted(tmp_path):
    df = _synthetic()
    root = _write_root(tmp_path, df)
    out = S.load_tdx_kline("600337.SH", root, adjust=True)
    assert "volume" in out.columns and "vol" not in out.columns
    assert len(out) == len(df)
    assert abs(out["close"].iloc[-1] - df["close"].iloc[-1] * 2.0) < 1e-6


def test_hypothesis_at_insufficient():
    df = _synthetic(n=100)
    out = S.hypothesis_at(df)
    assert out["ok"] is False and out["error"] == "insufficient_history"


def test_hypothesis_at_returns_primary():
    df = _synthetic(n=300)
    out = S.hypothesis_at(df)
    assert out["ok"] is True
    assert "primary" in out and "ops" in out
    assert isinstance(out["primary"], dict)


def test_no_adjust_keeps_raw_prices(tmp_path):
    df = _synthetic()
    root = _write_root(tmp_path, df)
    out = S.load_tdx_kline("600337.SH", root, adjust=False)
    assert abs(out["close"].iloc[-1] - df["close"].iloc[-1]) < 1e-9
