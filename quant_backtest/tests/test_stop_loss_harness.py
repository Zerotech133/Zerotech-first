"""tests/test_stop_loss_harness.py

工位 #370（门 N4 缠论止损实验）—— 独立实验工具的单元测试。
仅用合成价格序列，不触真实数据、不跑真实回测。
"""

import sys
from pathlib import Path

# 注入 experiments/ 目录到 sys.path（experiments 是新建目录、非包）。
QBT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(QBT / "experiments"))

from stop_loss_harness import (  # noqa: E402
    STOP_LOSS_EXPERIMENT_ENABLED,
    compare_strategies,
    default_params,
    simulate,
)


def test_baseline_monotonic_down():
    """确定性单调下跌序列：baseline 持有全程，亏损=全段跌幅，MDD=全段跌幅。"""
    prices = [100, 95, 90, 85, 80, 75]
    r = simulate(prices, "baseline")
    assert r["strategy"] == "baseline"
    # 全程跌幅 (75/100 - 1) = -0.25
    assert abs(r["final_pnl"] - (-0.25)) < 1e-6
    assert abs(r["max_drawdown"] - (-0.25)) < 1e-6
    assert r["exits"] == 0


def test_fixed_beats_baseline_on_down():
    """固定档默认 −5/−8/−12：提前止损离场，少亏于 baseline。"""
    prices = [100, 95, 90, 85, 80, 75]
    base = simulate(prices, "baseline")
    fixed = simulate(prices, "fixed")
    assert fixed["exits"] >= 1
    assert fixed["final_pnl"] > base["final_pnl"]


def test_trailing_bounds_drawdown_on_down():
    """移动止损 10%：MDD 被限制在约 -trailing_pct，且优于 baseline 全段跌幅。"""
    prices = [100, 95, 90, 85, 80, 75]
    base = simulate(prices, "baseline")
    trail = simulate(prices, "trailing")
    assert abs(trail["max_drawdown"] - (-0.10)) < 1e-6
    assert trail["max_drawdown"] > base["max_drawdown"]
    assert trail["exits"] >= 1


def test_compare_strategies_keys():
    """compare_strategies 返回三种策略。"""
    prices = [100, 95, 90, 85, 80, 75]
    res = compare_strategies(prices)
    assert set(res.keys()) == {"baseline", "fixed", "trailing"}


def test_default_params_keys():
    """default_params 含 fixed_tiers 与 trailing_pct 待批默认值。"""
    dp = default_params()
    assert "fixed_tiers" in dp
    assert "trailing_pct" in dp
    assert dp["fixed_tiers"] == [-0.05, -0.08, -0.12]
    assert abs(dp["trailing_pct"] - 0.10) < 1e-6


def test_experiment_activated_by_path_a():
    """路径 A 授权：STOP_LOSS_EXPERIMENT_ENABLED 已翻 True（仅激活对比能力，不接入生产交易）。"""
    assert STOP_LOSS_EXPERIMENT_ENABLED is True


def test_trailing_no_false_trigger_uptrend():
    """上行中仅小幅回撤（< trailing_pct），移动止损不应误触发离场。"""
    prices = [100, 110, 107, 112, 115]
    trail = simulate(prices, "trailing")
    assert trail["exits"] == 0
    # 全程上行收益 (115/100 - 1) = 0.15
    assert abs(trail["final_pnl"] - 0.15) < 1e-6


def test_trailing_oscillation_no_false_trigger():
    """震荡序列（规格示例的温和版）：先涨后小回撤再创新高，移动止损不应误触发离场。

    注：规格举例 [100,110,105,95,100] 实际会从峰值 110 回撤 13.6% 触发止损，
    与"上涨中无误触发(exits==0)"的断言矛盾；此处改用 [100,110,105,108,115]，
    其中 105 相对峰值 110 仅回撤 4.5%（< trailing_pct 10%），属震荡无误触发。
    """
    prices = [100, 110, 105, 108, 115]  # 震荡但每次回撤均 < 10%
    trail = simulate(prices, "trailing")
    assert trail["exits"] == 0
    # 全程上行收益 (115/100 - 1) = 0.15，接近持有收益
    assert abs(trail["final_pnl"] - 0.15) < 1e-6
    # 最大回撤应被限制在温和幅度（约 -4.5%），远优于大跌幅
    assert trail["max_drawdown"] > -0.06


def test_trailing_trigger_on_real_reversal():
    """真实大幅回撤（> trailing_pct）应触发移动止损离场。"""
    prices = [100, 110, 98, 95, 100]
    trail = simulate(prices, "trailing")
    # peak=110，98/110-1 ≈ -0.109 <=-0.10 → 触发离场
    assert trail["exits"] == 1
    assert trail["final_pnl"] < 0.0
