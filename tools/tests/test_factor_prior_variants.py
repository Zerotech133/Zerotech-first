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


def test_p2_weights_are_frozen():
    assert pv.P2_MOMENTUM_HORIZON_WEIGHTS == {5: 0.20, 21: 0.60, 60: 0.15, 120: 0.05}
    assert pv.P2_MOMENTUM_WEIGHTS == {
        "b1_mom_5d": 0.20,
        "b1_mom_21d": 0.60,
        "b1_mom_60d": 0.15,
        "b1_mom_120d": 0.05,
    }
    assert abs(sum(pv.P2_MOMENTUM_WEIGHTS.values()) - 1.0) < 1e-12
    assert pv.P2_WEIGHTS_SHA256 == "67646c0ae425d735fc1b5dfc689d91522a6f394fb5fbf17887ff9625bdf33edb"


def test_p2_only_replaces_momentum_sleeve():
    p2 = pv.get_prior("p2")
    assert p2.strategy_factors == pv.B1_P2_STRATEGY_FACTORS
    assert len(pv.P2_MOMENTUM_FACTORS) == 4
    assert len([n for n in p2.strategy_factors if n.startswith("b1_mom_")]) == 4
    assert len([n for n in p2.strategy_factors if n in MOMENTUM]) == 0
    for name in p2.strategy_factors:
        if name.startswith("b1_mom_"):
            assert p2.directions[name] == -1
        else:
            # 非动量方向沿用 P1/P0
            assert p2.directions[name] == ft.B1_PRIOR_DIRECTIONS[name]
    assert pv.get_prior("p0").directions == ft.B1_PRIOR_DIRECTIONS
    assert pv.get_prior("p1").directions == pv.B1_P1_PRIOR_DIRECTIONS


def test_p2_specs_are_horizon_factors():
    specs = pv.p2_momentum_specs()
    assert set(specs) == set(pv.P2_MOMENTUM_FACTORS)
    for name, spec in specs.items():
        assert spec.direction == -1
        assert spec.family == "Tushare-动量"
        assert spec.requires == ("adj_close",)
        assert name in spec.description


def test_p2_meta_serializable():
    meta = pv.prior_meta("p2")
    assert meta["version"] == "p2"
    assert meta["momentum_weights"] == pv.P2_MOMENTUM_WEIGHTS
    assert meta["p2_weights_sha256"] == pv.P2_WEIGHTS_SHA256
    assert len(meta["strategy_factors"]) == len(pv.B1_P2_STRATEGY_FACTORS)
    # P0/P1 元数据仍为 81 个策略因子
    assert len(pv.prior_meta("p0")["strategy_factors"]) == 81
    assert len(pv.prior_meta("p1")["strategy_factors"]) == 81


def test_p3_keeps_p1_sleeve_and_adds_tilt_only():
    p3 = pv.get_prior("p3")
    p1 = pv.get_prior("p1")
    assert p3.version == "p3"
    assert p3.directions == p1.directions
    assert p3.strategy_factors == p1.strategy_factors == pv.B1_P1_STRATEGY_FACTORS
    assert p3.momentum_weights is None
    assert p3.tilt_factor == pv.P3_TILT_FACTOR == "b1_mom_21d"
    assert p3.tilt_weight == pv.P3_TILT_WEIGHT == 0.075
    assert pv.P3_TILT_CANDIDATES == (0.05, 0.075, 0.10)
    # tilt 因子是 overlay，不进入方向表/策略因子表
    assert "b1_mom_21d" not in p3.directions
    assert "b1_mom_21d" not in p3.strategy_factors
    assert set(p3.directions) == set(ft.B1_PRIOR_DIRECTIONS)


def test_p3_tilt_candidate_and_meta():
    assert pv.get_prior("p3", tilt_weight=0.05).tilt_weight == 0.05
    assert pv.get_prior("p3", tilt_weight=0.10).tilt_weight == 0.10
    with pytest.raises(ValueError):
        pv.get_prior("p3", tilt_weight=0.20)
    meta = pv.prior_meta("p3", tilt_weight=0.075)
    assert meta["version"] == "p3"
    assert meta["tilt_weight"] == 0.075
    assert meta["tilt_factor"] == "b1_mom_21d"
    assert meta["tilt_candidates"] == [0.05, 0.075, 0.10]
    assert meta["p3_tilt_sha256"] == pv.P3_TILT_WEIGHTS_SHA256
    assert len(meta["strategy_factors"]) == 81


def test_p3_tilt_spec_is_21d_reversal():
    spec = pv.p3_tilt_spec()
    assert spec.name == "b1_mom_21d"
    assert spec.direction == -1
    assert spec.family == "Tushare-动量"
    assert spec.requires == ("adj_close",)


def test_invalid_version_raises():
    with pytest.raises(ValueError):
        pv.get_prior("p4")


def test_prior_meta_serializable():
    meta = pv.prior_meta("p1")
    assert meta["version"] == "p1"
    assert meta["n_directions"] == 86
    assert meta["n_changed_vs_p0"] == 18
    assert len(meta["strategy_factors"]) == 81
    assert isinstance(meta["changed_vs_p0"]["return_42d"], dict)
