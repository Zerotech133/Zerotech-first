# -*- coding: utf-8 -*-
"""AgentSignalAdapter 的隔离契约测试。"""

from types import MappingProxyType
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PROJECT_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "agent_platform" / "engine"))

from agent_platform.engine.strategy_registry import StrategyAgent, Verdict
from strategy_layer.adapters.agent_bridge import AgentSignalAdapter


class _FixedAgent(StrategyAgent):
    strategy_id = "agent_fixed"

    def __init__(
        self,
        recommendation,
        *,
        score=72.0,
        trace_id="",
        extras=None,
        rationale="Agent 依据",
    ):
        self.recommendation = recommendation
        self.score = score
        self.trace_id = trace_id
        self.extras = extras or {}
        self.rationale = rationale
        self.context = None

    def propose(self, context):
        self.context = context
        return Verdict(
            strategy_id=self.strategy_id,
            symbol=context["symbol"],
            recommendation=self.recommendation,
            confidence=0.8,
            score=self.score,
            rationale=self.rationale,
            trace_id=self.trace_id,
            extras=self.extras,
        )


@pytest.mark.parametrize(
    ("recommendation", "action"),
    [
        ("strong_buy", "buy"),
        ("buy", "buy"),
        ("hold", "hold"),
        ("sell", "sell"),
        ("strong_sell", "sell"),
    ],
)
def test_recommendation_maps_to_signal_action(recommendation, action):
    agent = _FixedAgent(recommendation)
    adapter = AgentSignalAdapter(
        agent, context_provider=lambda date: {"symbol": "600519.SH", "date": date}
    )

    signals = adapter.score("20261018")

    assert len(signals) == 1
    signal = signals[0]
    assert signal.action == action
    assert signal.symbol == "600519.SH"
    assert signal.score == 72.0
    assert signal.confidence == 0.8
    assert signal.strategy == "agent_fixed"
    assert signal.trade_date == "20261018"


def test_context_provider_is_called_and_context_is_read_only():
    source = {"symbol": "000001.SZ", "features": {"close": 12.3}}
    seen = []

    class ReadOnlyAgent(StrategyAgent):
        strategy_id = "readonly_agent"

        def propose(self, context):
            seen.append(context)
            with pytest.raises(TypeError):
                context["new_key"] = "must fail"
            with pytest.raises(TypeError):
                context["features"]["close"] = 0
            return Verdict(
                strategy_id=self.strategy_id,
                symbol=context["symbol"],
                recommendation="hold",
                confidence=0.5,
                score=50.0,
                rationale="只读上下文",
            )

    adapter = AgentSignalAdapter(
        ReadOnlyAgent(),
        context_provider=lambda trade_date: source,
    )
    signals = adapter.score("20261018")

    assert len(signals) == 1
    assert isinstance(seen[0], MappingProxyType)
    assert source == {"symbol": "000001.SZ", "features": {"close": 12.3}}


def test_trace_id_and_extras_are_preserved_in_rationale():
    adapter = AgentSignalAdapter(
        _FixedAgent("strong_sell", trace_id="trace-42", extras={"model": "m1"}),
        context_provider=lambda _: {"symbol": "00700.HK"},
    )

    signal = adapter.score("20261018")[0]

    assert "trace-42" in signal.rationale
    assert "model" in signal.rationale
    assert "m1" in signal.rationale


def test_agent_exception_is_visible_and_degrades_to_empty(capsys):
    class BrokenAgent(StrategyAgent):
        strategy_id = "broken_agent"

        def propose(self, context):
            raise RuntimeError("agent boom")

    adapter = AgentSignalAdapter(BrokenAgent(), context_provider=lambda _: {})

    assert adapter.score("20261018") == []
    assert "agent boom" in capsys.readouterr().out


def test_invalid_verdict_degrades_to_empty(capsys):
    class InvalidAgent(StrategyAgent):
        strategy_id = "invalid_agent"

        def propose(self, context):
            return Verdict(
                strategy_id=self.strategy_id,
                symbol="600519.SH",
                recommendation="hold",
                confidence=0.5,
                score=101.0,
                rationale="越界分数",
            )

    adapter = AgentSignalAdapter(InvalidAgent())

    assert adapter.score("20261018") == []
    assert "101" in capsys.readouterr().out
