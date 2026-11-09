"""tools/tests/test_b1_prior_directions.py · B1 先验方向重定义单测。

先验方向必须在查看 IC/回测结果之前固定；本测试只校验代码里硬编码的映射，
不读取评测结果，避免用样本内 IC 反推方向。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))

import factor_lib  # noqa: E402
import factor_lib_tushare as ft  # noqa: E402


def test_b1_prior_covers_all_b1_factors():
    b1 = {
        n for n, e in ft.TUSHARE_FACTOR_MAP.items()
        if e["batch"] == ft.BATCH_B1 and e["status"] == "implemented"
    }
    assert len(b1) == 86
    assert set(ft.B1_PRIOR_DIRECTIONS) == b1
    assert set(ft.B1_PRIOR_RATIONALE) == b1
    assert all(d in (1, -1) for d in ft.B1_PRIOR_DIRECTIONS.values())
    assert set(ft.B1_PRIOR_UNKNOWN) <= b1


def test_prior_overrides_are_applied_before_registration():
    expected = {
        "return_5d": -1,
        "return_21d": -1,
        "amount_ma_20d": -1,
        "sum_abs_rtn_amount_20d": 1,
        "log_price": -1,
        "days_beyond_upper_lower_21d": -1,
    }
    for name, direction in expected.items():
        assert factor_lib.FACTORS[name].direction == direction, name
        assert ft.B1_PRIOR_DIRECTIONS[name] == direction, name
        assert name not in ft.B1_PRIOR_UNKNOWN


def test_non_override_formula_directions_are_preserved():
    # 这些因子的公式自带负号或正向含义，不能被因子族默认方向覆盖。
    expected = {
        "rsi": -1,
        "sharpe_60d": 1,
        "sharpe_750d": 1,
        "adjusted_sharpe_750d": 1,
        "turnover_ma_20d": 1,
        "turnover_ma_20d_120d": 1,
        "small_cap_reversal_21d": 1,
        "return_42d": 1,
        "return_252d": 1,
        "beta_60d_000300": -1,
        "return_std_21d": -1,
    }
    for name, direction in expected.items():
        assert factor_lib.FACTORS[name].direction == direction, name


def test_unknown_prior_factors_are_excluded_from_strategy():
    assert ft.B1_PRIOR_UNKNOWN == {
        "days_down_up",
        "volume_alpha_300d_000001",
        "volume_alpha_300d_000300",
        "nl_size",
        "price_dist",
    }
    assert len(ft.B1_STRATEGY_FACTORS) == 81
    assert set(ft.B1_STRATEGY_FACTORS).isdisjoint(ft.B1_PRIOR_UNKNOWN)
    for name in ft.B1_STRATEGY_FACTORS:
        assert factor_lib.FACTORS[name].direction == ft.B1_PRIOR_DIRECTIONS[name]


def test_prior_rationale_mentions_override_reason():
    assert "反转" in ft.B1_PRIOR_RATIONALE["return_5d"]
    assert "成交额" in ft.B1_PRIOR_RATIONALE["amount_ma_20d"]
    assert "低价" in ft.B1_PRIOR_RATIONALE["log_price"]
    assert "超买" in ft.B1_PRIOR_RATIONALE["days_beyond_upper_lower_21d"]
    assert "无稳定先验" in ft.B1_PRIOR_RATIONALE["price_dist"]
