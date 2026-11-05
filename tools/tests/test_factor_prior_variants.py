"""P0/P1 先验版本隔离测试：P1 不得改写 P0。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))

import factor_lib_tushare as ft  # noqa: E402
import factor_prior_variants as pv  # noqa: E402


MOMENTUM = {
    name for name in ft.B1_PRIOR_DIRECTIONS
    if (ft.TUSHARE_FACTOR_MAP.get(name) or {}).get("factor_type") == "Momentum"
}


def test_p0_is_default_and_unchanged():
    p0 = pv.get_prior("p0")
    assert p0.version == "p0"
    assert p0.directions == ft.B1_PRIOR_DIRECTIONS
    assert p0.changed_vs_p0 == {}
    # 构建 P1 不能改写 P0 的全局映射
    pv.get_prior("p1")
    assert p0.directions == ft.B1_PRIOR_DIRECTIONS
    assert ft.B1_PRIOR_DIRECTIONS["return_42d"] == 1
    assert ft.B1_PRIOR_DIRECTIONS["return_5d"] == -1


def test_p1_flips_only_momentum_family():
    p1 = pv.get_prior("p1")
    assert set(p1.directions) == set(ft.B1_PRIOR_DIRECTIONS)
    for name in ft.B1_PRIOR_DIRECTIONS:
        expected = -1 if name in MOMENTUM else ft.B1_PRIOR_DIRECTIONS[name]
        assert p1.directions[name] == expected, name
    assert len(MOMENTUM) == 20
    assert all(p1.directions[name] == -1 for name in MOMENTUM)


def test_p1_does_not_change_unknown_or_strategy_set():
    p1 = pv.get_prior("p1")
    assert set(p1.directions) - set(ft.B1_PRIOR_UNKNOWN) == set(ft.B1_STRATEGY_FACTORS)
    assert set(pv.B1_P1_STRATEGY_FACTORS) == set(ft.B1_STRATEGY_FACTORS)
    assert pv.B1_P1_STRATEGY_FACTORS == ft.B1_STRATEGY_FACTORS
    assert "days_down_up" in ft.B1_PRIOR_UNKNOWN


def test_p1_changed_map_exact():
    changed = pv.B1_PRIOR_CHANGED_P1
    assert len(changed) == 18
    assert changed["return_42d"] == {"p0": 1, "p1": -1}
    assert "return_5d" not in changed
    # 未知因子 days_down_up 也被反转，但不进入策略
    assert changed["days_down_up"] == {"p0": 1, "p1": -1}
    # 非动量因子完全不变
    assert "avg_turnover_20d" not in changed
    assert "rsi" not in changed


def test_strategy_changed_factor_count():
    changed_strategy = [
        name for name in pv.B1_P1_STRATEGY_FACTORS
        if pv.B1_P1_PRIOR_DIRECTIONS[name] != ft.B1_PRIOR_DIRECTIONS[name]
    ]
    assert len(changed_strategy) == 17  # 18 个动量反转 - days_down_up（unknown）


def test_invalid_version_raises():
    with pytest.raises(ValueError):
        pv.get_prior("p2")


def test_prior_meta_serializable():
    meta = pv.prior_meta("p1")
    assert meta["version"] == "p1"
    assert meta["n_directions"] == 86
    assert meta["n_changed_vs_p0"] == 18
    assert len(meta["strategy_factors"]) == 81
    assert isinstance(meta["changed_vs_p0"]["return_42d"], dict)
