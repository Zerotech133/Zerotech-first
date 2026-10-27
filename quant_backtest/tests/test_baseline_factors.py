# -*- coding: utf-8 -*-
"""baseline_factors MVP 测试（WO-LCS1-BASELINE-20261027）。

覆盖：因子公式、PIT（未来截断不变）、前向收益、IC/RankIC、分组、CLI dry-run。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parents[1]          # quant_backtest/
_TOOL = _HERE / "baseline_factors.py"
_spec = importlib.util.spec_from_file_location("baseline_factors", str(_TOOL))
BF = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(BF)


def _panel(days: int = 120, codes=("000001.SZ", "600519.SH", "300750.SZ")) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-01", periods=days).strftime("%Y%m%d").tolist()
    rows = []
    for k, code in enumerate(codes):
        rng = np.random.default_rng(100 + k)
        rets = rng.normal(0.0005, 0.012, days)
        close = 10.0 * np.cumprod(1 + rets)
        openp = close * (1 + rng.normal(0, 0.003, days))
        rows.append(pd.DataFrame({
            "ts_code": code, "trade_date": dates,
            "open": openp, "high": np.maximum(openp, close) * 1.01,
            "low": np.minimum(openp, close) * 0.99, "close": close,
            "vol": rng.integers(10000, 50000, days).astype(float),
            "amount": rng.uniform(1e5, 5e5, days),
            "adj_factor": 1.0,
        }))
    df = pd.concat(rows, ignore_index=True)
    for col in ("open", "high", "low", "close"):
        df["adj_" + col] = df[col] * df["adj_factor"]
    return df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)


def test_factor_columns_and_mom_formula():
    df = _panel()
    out = BF.compute_factors(df, turnover_source="proxy_amount20")
    for f in BF.FACTORS:
        assert f in out.columns, f
    one = out[out["ts_code"] == "000001.SZ"].reset_index(drop=True)
    expected = one.loc[10, "adj_close"] / one.loc[5, "adj_close"] - 1
    assert abs(one.loc[10, "MOM5"] - expected) < 1e-12
    # 前 5 日 MOM5 应为 NaN
    assert pd.isna(one.loc[3, "MOM5"])


def test_pit_truncation_invariance():
    full = _panel(days=120)
    full = BF.compute_factors(full)
    cut_date = sorted(full["trade_date"].unique())[80]
    trunc = full[full["trade_date"] <= cut_date].copy()
    trunc = BF.compute_factors(trunc)
    keys = ["ts_code", "trade_date"]
    cols = ["MOM5", "MOM20", "VOL20", "RSV20", "RSI14"]
    merged = full[keys + cols].merge(trunc[keys + cols], on=keys, suffixes=("_full", "_trunc"))
    for c in cols:
        a = merged[c + "_full"]
        b = merged[c + "_trunc"]
        both = a.notna() & b.notna()
        assert both.any()
        assert np.allclose(a[both], b[both], equal_nan=True), c


def test_forward_return_entry_next_open():
    df = _panel()
    out = BF.add_forward_returns(df, [5])
    one = out[out["ts_code"] == "000001.SZ"].reset_index(drop=True)
    expected = one.loc[6, "adj_open"] / one.loc[1, "adj_open"] - 1
    assert abs(one.loc[0, "fwd_ret_5"] - expected) < 1e-12


def test_ic_and_rank_ic_perfect():
    df = _panel(days=60, codes=("A.SZ", "B.SZ", "C.SZ", "D.SZ", "E.SZ"))
    df = BF.compute_factors(df)
    df = BF.add_forward_returns(df, [5])
    df["TESTF"] = df["fwd_ret_5"]
    # 每天 5 只 -> min_obs=3 可算
    ic = BF.cross_section_ic(df, "TESTF", "fwd_ret_5", min_obs=3)
    assert len(ic) > 0
    assert (ic["ic"] > 0.999).all()
    assert (ic["rank_ic"] > 0.999).all()
    summ = BF.factor_summary(ic)
    assert summ["ic_win_rate"] == 1.0


def test_group_returns():
    df = _panel(days=80, codes=("A.SZ", "B.SZ", "C.SZ", "D.SZ", "E.SZ"))
    df = BF.compute_factors(df)
    df = BF.add_forward_returns(df, [5])
    g = BF.group_returns(df, "MOM20", "fwd_ret_5", q=5, min_obs=5)
    assert g["n_days"] > 0
    assert len(g["groups"]) == 5
    assert g["long_short"] is not None


def test_cli_dry_run_zero_write(tmp_path):
    out = tmp_path / "out"
    r = subprocess.run(
        [sys.executable, "-X", "utf8", str(_TOOL), "--dry-run",
         "--data-root", str(tmp_path / "nodata"), "--out", str(out)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    plan = json.loads(r.stdout)["plan"]
    assert plan["data_ok"] is False
    assert not out.exists()
