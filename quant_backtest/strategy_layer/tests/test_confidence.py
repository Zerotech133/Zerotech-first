# -*- coding: utf-8 -*-
"""confidence.py 单元测试：三规则边界 + 组合传播 + 样本量收敛。

纯函数测试，不连接 PG。
运行：python -X utf8 -m pytest strategy_layer/tests/test_confidence.py -v
"""

import math
import sys
from pathlib import Path

import pytest

# 允许从 strategy_layer 目录直接导入 confidence 模块
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from confidence import (  # noqa: E402
    CONF_FLOOR,
    HALF_LIFE_HOURS,
    compute_hop_confidence,
    score_to_confidence,
)


# ---------------------------------------------------------------------------
# 规则 1：时间衰减
# ---------------------------------------------------------------------------
class TestTimeDecay:
    """时间衰减规则边界。"""

    def test_zero_age_no_decay(self):
        """0 年龄不衰减，返回上游置信本身。"""
        assert compute_hop_confidence(0.8, 0.0, "quote") == pytest.approx(0.8)

    def test_one_half_life_exactly_half(self):
        """恰好一个半衰期 → 置信减半。"""
        for cat, hl in HALF_LIFE_HOURS.items():
            assert compute_hop_confidence(0.8, hl, cat) == pytest.approx(0.4)

    def test_two_half_lives_quarter(self):
        """两个半衰期 → 置信为 1/4。"""
        assert compute_hop_confidence(0.8, 8.0, "quote") == pytest.approx(0.2)

    def test_extreme_age_hits_floor(self):
        """超长年龄触底：不低于 CONF_FLOOR=0.1。"""
        result = compute_hop_confidence(1.0, 10_000.0, "quote")
        assert result == pytest.approx(CONF_FLOOR)
        # kg 半衰期最长（720h），10000h ≈ 13.9 个半衰期也已衰减触底；
        # 取 2 个半衰期（1440h）验证未触底
        assert compute_hop_confidence(1.0, 10_000.0, "kg") == pytest.approx(CONF_FLOOR)
        assert compute_hop_confidence(1.0, 1440.0, "kg") == pytest.approx(0.25)

    def test_negative_age_no_boost(self):
        """负年龄（未来时间戳）按 0 处理，不产生增益。"""
        assert compute_hop_confidence(0.5, -100.0, "news") == pytest.approx(0.5)

    def test_floor_applies_even_from_high_source(self):
        """上游置信为 1、年龄极大，仍触底 0.1 而非 0。"""
        assert compute_hop_confidence(1.0, 1e9, "kg") == pytest.approx(CONF_FLOOR)

    def test_unknown_category_raises(self):
        """未知品类抛 ValueError。"""
        with pytest.raises(ValueError):
            compute_hop_confidence(0.5, 0.0, "unknown_cat")

    def test_source_clamped_to_unit_interval(self):
        """上游置信越界被夹取到 [0,1]；负置信夹 0 后经时间衰减下限托至 CONF_FLOOR。"""
        assert compute_hop_confidence(1.5, 0.0, "quote") == pytest.approx(1.0)
        assert compute_hop_confidence(-0.2, 0.0, "quote") == pytest.approx(CONF_FLOOR)


# ---------------------------------------------------------------------------
# 规则 2：融合增益
# ---------------------------------------------------------------------------
class TestDualSourceGain:
    """融合增益规则边界。"""

    def test_agree_boost_capped_at_one(self):
        """双源一致增益封顶 1.0：0.95 * 1.15 = 1.0925 → 1.0。"""
        assert compute_hop_confidence(0.95, 0.0, "kg", dual_source_agree=True) == pytest.approx(1.0)

    def test_agree_boost_normal(self):
        """低置信 + 双源一致：0.6 * 1.15 = 0.69。"""
        assert compute_hop_confidence(0.6, 0.0, "kg", dual_source_agree=True) == pytest.approx(0.69)

    def test_conflict_discount(self):
        """双源冲突折价 0.7：0.6 * 0.7 = 0.42。"""
        assert compute_hop_confidence(0.6, 0.0, "kg", dual_source_agree=False) == pytest.approx(0.42)

    def test_none_no_change(self):
        """None（无双源信息）不变。"""
        assert compute_hop_confidence(0.6, 0.0, "kg") == pytest.approx(0.6)
        assert compute_hop_confidence(0.6, 0.0, "kg", dual_source_agree=None) == pytest.approx(0.6)

    def test_agree_never_exceeds_one_even_after_gain(self):
        """任意输入下增益结果不超过 1.0。"""
        assert compute_hop_confidence(0.9999, 0.0, "news", dual_source_agree=True) <= 1.0


# ---------------------------------------------------------------------------
# 规则 3：事故惩罚
# ---------------------------------------------------------------------------
class TestIncidentPenalty:
    """事故惩罚规则边界。"""

    def test_zero_penalty_no_change(self):
        """惩罚 0 不变。"""
        assert compute_hop_confidence(0.8, 0.0, "quote", incident_penalty=0.0) == pytest.approx(0.8)

    def test_full_penalty_zeroes_confidence(self):
        """惩罚 1.0 → 置信归零（可击穿时间衰减下限）。"""
        result = compute_hop_confidence(1.0, 0.0, "kg", incident_penalty=1.0)
        assert result == pytest.approx(0.0)

    def test_half_penalty_halves(self):
        """惩罚 0.5 → 置信减半。"""
        assert compute_hop_confidence(0.8, 0.0, "quote", incident_penalty=0.5) == pytest.approx(0.4)

    def test_penalty_clamped(self):
        """越界惩罚被夹取到 [0,1]。"""
        assert compute_hop_confidence(0.8, 0.0, "quote", incident_penalty=2.0) == pytest.approx(0.0)
        assert compute_hop_confidence(0.8, 0.0, "quote", incident_penalty=-1.0) == pytest.approx(0.8)

    def test_penalty_can_break_floor(self):
        """惩罚可击穿 CONF_FLOOR 下限（floor 仅作用于时间衰减规则）。"""
        result = compute_hop_confidence(1.0, 1e9, "quote", incident_penalty=1.0)
        assert result == pytest.approx(0.0)
        assert result < CONF_FLOOR


# ---------------------------------------------------------------------------
# 组合传播：三规则叠加
# ---------------------------------------------------------------------------
class TestCombinedPropagation:
    """组合传播与单调性。"""

    def test_full_chain_order(self):
        """衰减→增益→惩罚 的确定性组合结果。"""
        # 0.8, quote, 4h(1个半衰期)=0.4 → 双源一致 *1.15=0.46 → 惩罚 0.2 → 0.368
        assert compute_hop_confidence(
            0.8, 4.0, "quote", dual_source_agree=True, incident_penalty=0.2
        ) == pytest.approx(0.368)

    def test_monotonic_in_source_conf(self):
        """上游置信越高，传播结果越高（固定其他参数）。"""
        results = [
            compute_hop_confidence(s, 10.0, "news", dual_source_agree=True, incident_penalty=0.1)
            for s in (0.1, 0.3, 0.5, 0.7, 0.9)
        ]
        assert results == sorted(results)

    def test_monotonic_in_freshness(self):
        """数据越新置信越高（固定其他参数）。"""
        results = [
            compute_hop_confidence(0.9, h, "fundamental", incident_penalty=0.05)
            for h in (0.0, 50.0, 100.0, 168.0, 500.0)
        ]
        assert all(a >= b for a, b in zip(results, results[1:])) or results == sorted(
            results, reverse=True
        )
        # 严格递减（除触底段外）由衰减公式保证
        assert results[0] > results[-1]

    def test_order_of_rules_matters_vs_naive(self):
        """增益作用于衰减后、惩罚作用于增益后——与一次性相乘结果可区分。"""
        # 手工按规则顺序：0.8 → 衰减4h(quote)=0.4 → 增益0.46 → 惩罚0 → 0.46
        chained = compute_hop_confidence(0.8, 4.0, "quote", dual_source_agree=True)
        assert chained == pytest.approx(0.46)
        # 若先增益后衰减会得 0.92*0.5=0.46（此处相同），再用惩罚验证顺序：
        # 先衰减再增益再惩罚 0.5：0.4*1.15=0.46 → 0.23
        penalized = compute_hop_confidence(
            0.8, 4.0, "quote", dual_source_agree=True, incident_penalty=0.5
        )
        assert penalized == pytest.approx(0.23)

    def test_all_categories_valid_input(self):
        """四类品类均可用同一契约调用。"""
        for cat in HALF_LIFE_HOURS:
            result = compute_hop_confidence(0.9, 12.0, cat)
            assert CONF_FLOOR <= result <= 1.0
            assert math.isfinite(result)


# ---------------------------------------------------------------------------
# score_to_confidence：样本量收敛性
# ---------------------------------------------------------------------------
class TestScoreToConfidence:
    """策略分+样本量 → 情报层置信。"""

    def test_zero_sample_gives_zero(self):
        """样本量 0 → 样本置信项为 0 → 总置信 0。"""
        assert score_to_confidence(90.0, 0) == pytest.approx(0.0)

    def test_sample_scale_gives_half_sample_term(self):
        """sample_n=100 → 样本置信项 0.5：conf = score/100 * 0.5。"""
        assert score_to_confidence(80.0, 100) == pytest.approx(0.4)

    def test_sample_convergence(self):
        """样本量增大，置信单调递增并收敛（不超过 score/100）。"""
        results = [score_to_confidence(85.0, n) for n in (0, 10, 50, 100, 500, 5000, 100_000)]
        assert all(a < b for a, b in zip(results, results[1:]))
        assert results[-1] == pytest.approx(0.85, abs=1e-3)

    def test_score_zero_gives_zero(self):
        """策略分 0 → 置信 0。"""
        assert score_to_confidence(0.0, 1000) == pytest.approx(0.0)

    def test_output_bounded(self):
        """任意输入下输出在 [0,1]。"""
        assert 0.0 <= score_to_confidence(100.0, 10**9) <= 1.0
        assert 0.0 <= score_to_confidence(50.0, 0) <= 1.0

    def test_score_clamped(self):
        """策略分越界被夹取。"""
        assert score_to_confidence(150.0, 10**9) <= 1.0
        assert score_to_confidence(-5.0, 10**9) == pytest.approx(0.0)
