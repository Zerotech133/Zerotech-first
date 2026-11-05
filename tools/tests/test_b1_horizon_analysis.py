"""b1_horizon_analysis 单元测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))

import b1_horizon_analysis as hz  # noqa: E402
from factor_lib import Panel  # noqa: E402


def _panel() -> Panel:
    dates = ["20230103", "20230104", "20230105", "20230106", "20230109", "20230110"]
    codes = ["000001.SZ", "000002.SZ"]
    close = pd.DataFrame(
        [[10.0, 20.0], [10.5, 20.2], [11.0, 20.4], [10.8, 20.6], [11.2, 20.8], [11.5, 21.0]],
        index=dates, columns=codes,
    )
    adj = pd.DataFrame(
        [[10.0, 20.0], [10.5, 20.2], [11.0, 20.4], [10.8, 20.6], [11.2, 20.8], [11.5, 21.0]],
        index=dates, columns=codes,
    )
    return Panel({"close": close, "adj_close": adj})


def test_horizon_specs_are_reversal_and_use_exact_lookback():
    specs = hz.horizon_specs()
    assert set(specs) == {"b1_mom_5d", "b1_mom_21d", "b1_mom_60d", "b1_mom_120d"}
    assert all(s.direction == -1 for s in specs.values())
    p = _panel()
    out = specs["b1_mom_5d"].compute(p)
    expected = p.fields["adj_close"].iloc[-1] / p.fields["adj_close"].iloc[-6] - 1.0
    pd.testing.assert_series_equal(out.iloc[-1], expected, check_names=False)
    assert out.iloc[:5].isna().all().all()


def test_direction_advice_uses_holdout_significance():
    assert hz._direction_advice(
        {"ic_reversal_mean": 0.05},
        {"ic_reversal_mean": 0.09},
        {"ic_reversal_mean": 0.08, "ic_reversal_t": 10.0},
    )["direction"] == -1
    assert hz._direction_advice(
        {"ic_reversal_mean": -0.05},
        {"ic_reversal_mean": -0.09},
        {"ic_reversal_mean": -0.08, "ic_reversal_t": -10.0},
    )["direction"] == 1
    assert hz._direction_advice(
        {"ic_reversal_mean": 0.0},
        {"ic_reversal_mean": 0.0},
        {"ic_reversal_mean": 0.01, "ic_reversal_t": 0.5},
    )["direction"] is None
