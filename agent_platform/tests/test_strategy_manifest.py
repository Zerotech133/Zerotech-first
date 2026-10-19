# -*- coding: utf-8 -*-
"""StrategyAgent 最小插件元数据契约测试。"""
import sys
from pathlib import Path

import pytest

PLATFORM = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLATFORM / "engine"))

from strategy_registry import (  # noqa: E402
    LIFECYCLE_STATES, StrategyAgent, StrategyError, StrategyRegistry,
)


class _LegacyAgent(StrategyAgent):
    strategy_id = "legacy_agent"
    stance = "bull"

    def propose(self, context):
        raise NotImplementedError


class _ManifestAgent(StrategyAgent):
    strategy_id = "manifest_agent"
    version = "2.1.0"
    family = "trend"
    capability_manifest = ("market_data", "technical_analysis")
    lifecycle_state = "experimental"
    stance = "neutral"

    def propose(self, context):
        raise NotImplementedError


def _agent(**attrs):
    return type("_ConfiguredAgent", (StrategyAgent,), {
        "strategy_id": "configured_agent",
        "stance": "neutral",
        **attrs,
    })()


def test_legacy_agent_receives_compatible_defaults():
    registry = StrategyRegistry()
    registry.register(_LegacyAgent())

    assert registry.metadata("legacy_agent") == {
        "strategy_id": "legacy_agent",
        "version": "1.0.0",
        "family": "legacy",
        "capability_manifest": [],
        "lifecycle_state": "active",
    }


def test_register_and_query_manifest():
    registry = StrategyRegistry()
    registry.register(_ManifestAgent())

    assert registry.metadata("manifest_agent") == {
        "strategy_id": "manifest_agent",
        "version": "2.1.0",
        "family": "trend",
        "capability_manifest": ["market_data", "technical_analysis"],
        "lifecycle_state": "experimental",
    }
    assert registry.list_manifests() == [registry.metadata("manifest_agent")]
    assert registry.list_manifests("experimental") == registry.list_manifests()
    assert registry.list_manifests("active") == []


def test_metadata_is_a_snapshot():
    registry = StrategyRegistry()
    registry.register(_ManifestAgent())
    manifest = registry.metadata("manifest_agent")
    manifest["capability_manifest"].append("mutated")

    assert registry.metadata("manifest_agent")["capability_manifest"] == [
        "market_data", "technical_analysis"
    ]


@pytest.mark.parametrize("field,value", [
    ("version", ""),
    ("version", 1),
    ("family", ""),
    ("family", 1),
    ("capability_manifest", "market_data"),
    ("capability_manifest", ("",)),
])
def test_register_rejects_invalid_manifest_fields(field, value):
    with pytest.raises(StrategyError, match=field):
        StrategyRegistry().register(_agent(**{field: value}))


def test_register_and_query_reject_invalid_lifecycle_state():
    with pytest.raises(StrategyError, match="lifecycle_state"):
        StrategyRegistry().register(_agent(lifecycle_state="paused"))

    registry = StrategyRegistry()
    registry.register(_LegacyAgent())
    with pytest.raises(StrategyError, match="lifecycle_state"):
        registry.list_manifests("paused")


def test_lifecycle_states_are_explicit_and_usable():
    assert set(LIFECYCLE_STATES) == {
        "experimental", "active", "deprecated", "retired"
    }
    for state in LIFECYCLE_STATES:
        agent = _agent(strategy_id="agent_" + state, lifecycle_state=state)
        registry = StrategyRegistry()
        registry.register(agent)
        assert registry.metadata(agent.strategy_id)["lifecycle_state"] == state
