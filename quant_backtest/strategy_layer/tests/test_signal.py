# -*- coding: utf-8 -*-
"""Signal 构造校验测试：非法 action / 越界分数 / 空 rationale 等。"""
import pytest

from strategy_layer.signal import Signal, VALID_ACTIONS


def _kwargs(**over):
    base = dict(
        symbol="000338.SZ",
        action="buy",
        score=75.0,
        confidence=0.8,
        rationale="图谱社区热度：关联概念 白酒/超级品牌",
        strategy="kg_boost",
        trade_date="20261016",
    )
    base.update(over)
    return base


class TestValidConstruction:
    def test_minimal_valid(self):
        s = Signal(**_kwargs())
        assert s.symbol == "000338.SZ"
        assert s.action == "buy"
        assert s.score == 75.0
        assert s.confidence == 0.8
        assert s.strategy == "kg_boost"
        assert s.trade_date == "20261016"

    @pytest.mark.parametrize("action", VALID_ACTIONS)
    def test_all_four_actions_legal(self, action):
        s = Signal(**_kwargs(action=action))
        assert s.action == action

    def test_boundary_scores(self):
        Signal(**_kwargs(score=0.0, confidence=0.0))
        Signal(**_kwargs(score=100.0, confidence=1.0))


class TestInvalidConstruction:
    def test_illegal_action(self):
        with pytest.raises(ValueError):
            Signal(**_kwargs(action="strong_buy"))

    def test_empty_action(self):
        with pytest.raises(ValueError):
            Signal(**_kwargs(action=""))

    def test_score_above_100(self):
        with pytest.raises(ValueError):
            Signal(**_kwargs(score=100.5))

    def test_score_below_0(self):
        with pytest.raises(ValueError):
            Signal(**_kwargs(score=-0.1))

    def test_score_not_number(self):
        with pytest.raises(ValueError):
            Signal(**_kwargs(score="75"))

    def test_confidence_above_1(self):
        with pytest.raises(ValueError):
            Signal(**_kwargs(confidence=1.2))

    def test_confidence_below_0(self):
        with pytest.raises(ValueError):
            Signal(**_kwargs(confidence=-0.01))

    def test_empty_rationale(self):
        with pytest.raises(ValueError):
            Signal(**_kwargs(rationale=""))

    def test_whitespace_rationale(self):
        with pytest.raises(ValueError):
            Signal(**_kwargs(rationale="   "))

    def test_empty_symbol(self):
        with pytest.raises(ValueError):
            Signal(**_kwargs(symbol=""))

    def test_empty_strategy(self):
        with pytest.raises(ValueError):
            Signal(**_kwargs(strategy=""))

    def test_bad_trade_date_format(self):
        for bad in ("2026-10-16", "2026101", "202610167", "abc", ""):
            with pytest.raises(ValueError):
                Signal(**_kwargs(trade_date=bad))
