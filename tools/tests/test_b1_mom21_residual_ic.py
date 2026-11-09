# -*- coding: utf-8 -*-
"""b1_mom21_residual_ic 的截面正交化单元测试。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))

from b1_mom21_residual_ic import _daily_rank_corr, _directed_rank, _orthogonalize  # noqa: E402


def _frames(n_dates: int = 4, n_codes: int = 80) -> tuple[pd.DataFrame, ...]:
    rng = np.random.default_rng(20261106)
    dates = pd.Index([f"2026010{i + 1}" for i in range(n_dates)])
    codes = pd.Index([f"{i:06d}.SZ" for i in range(n_codes)])
    a = pd.DataFrame(rng.normal(size=(n_dates, n_codes)), index=dates, columns=codes)
    b = pd.DataFrame(rng.normal(size=(n_dates, n_codes)), index=dates, columns=codes)
    eligible = pd.DataFrame(True, index=dates, columns=codes)
    return a, b, eligible


def test_directed_rank_flips_direction() -> None:
    raw = pd.DataFrame([[1.0, 2.0, 3.0, np.nan]], columns=list("abcd"))
    rev = _directed_rank(raw, -1)
    assert np.isnan(rev.iloc[0, 3])
    # 方向 -1 时，原始值最大者 rank 最低
    assert rev.iloc[0, 2] < rev.iloc[0, 1] < rev.iloc[0, 0]


def test_orthogonalize_same_factor_leaves_zero_residual() -> None:
    a, _, eligible = _frames()
    y = _directed_rank(a, -1)
    resid, diag = _orthogonalize(y, [y], eligible)
    assert diag["n_days"] == 4
    assert diag["max_abs_residual"] < 1e-12
    assert np.nanmax(np.abs(resid.to_numpy())) < 1e-12


def test_orthogonalize_residual_is_orthogonal_to_x() -> None:
    a, b, eligible = _frames()
    y = _directed_rank(a, 1)
    x = _directed_rank(b, 1)
    resid, diag = _orthogonalize(y, [x], eligible)
    assert diag["n_days"] == 4
    assert diag["max_abs_residual"] > 1e-8

    arr_y = y.to_numpy(dtype=float)
    arr_x = x.to_numpy(dtype=float)
    arr_r = resid.to_numpy(dtype=float)
    for i in range(arr_y.shape[0]):
        m = np.isfinite(arr_x[i]) & np.isfinite(arr_r[i])
        xc = arr_x[i, m] - arr_x[i, m].mean()
        rc = arr_r[i, m] - arr_r[i, m].mean()
        assert abs(float(np.dot(xc, rc))) < 1e-8 * max(1.0, float(np.abs(xc).sum()))


def test_daily_rank_corr_identity() -> None:
    a, _, eligible = _frames()
    b = a * 2.0 + 3.0
    corr = _daily_rank_corr(a, b, eligible)
    assert corr["n_days"] == 4
    assert corr["mean"] == pytest.approx(1.0)
    assert corr["min"] == pytest.approx(1.0)
