# -*- coding: utf-8 -*-
"""StrategyAgent 扩展插槽验收契约测试。

用三个最小插件锁定：插件自带元数据即可被 Registry 发现；每个插件可独立
运行；Registry 可按生命周期筛选；新增第三个插件不需要改公共收集主链。
"""

import sys
from pathlib import Path

PLATFORM = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLATFORM / "engine"))
sys.path.insert(0, str(PLATFORM))

from strategy_registry import StrategyAgent, StrategyRegistry, Verdict  # noqa: E402
import run_shadow  # noqa: E402


class _FirstAgent(StrategyAgent):
    strategy_id = "contract_first"
    version = "1.0.0"
    family = "contract"
    capability_manifest = ("first",)
    lifecycle_state = "active"
    stance = "bull"

    def propose(self, context):
        return Verdict(
            strategy_id=self.strategy_id,
            symbol=context["symbol"],
            recommendation="buy",
            confidence=0.8,
            stance=self.stance,
        )


class _SecondAgent(StrategyAgent):
    strategy_id = "contract_second"
    version = "1.0.0"
    family = "contract"
    capability_manifest = ("second",)
    lifecycle_state = "experimental"
    stance = "neutral"

    def propose(self, context):
        return Verdict(
            strategy_id=self.strategy_id,
            symbol=context["symbol"],
            recommendation="hold",
            confidence=0.5,
            stance=self.stance,
        )


class _ThirdAgent(StrategyAgent):
    strategy_id = "contract_third"
    version = "1.0.0"
    family = "contract"
    capability_manifest = ("third",)
    lifecycle_state = "deprecated"
    stance = "bear"

    def propose(self, context):
        return Verdict(
            strategy_id=self.strategy_id,
            symbol=context["symbol"],
            recommendation="sell",
            confidence=0.7,
            stance=self.stance,
        )


def _registry(*agents):
    registry = StrategyRegistry()
    for agent in agents:
        registry.register(agent)
    return registry


_CONTEXT = {"symbol": "600519"}


def test_registry_discovers_and_runs_three_metadata_only_plugins():
    registry = _registry(_FirstAgent(), _SecondAgent(), _ThirdAgent())

    assert registry.list_ids() == [
        "contract_first",
        "contract_second",
        "contract_third",
    ]
    assert registry.metadata("contract_third") == {
        "strategy_id": "contract_third",
        "version": "1.0.0",
        "family": "contract",
        "capability_manifest": ["third"],
        "lifecycle_state": "deprecated",
    }

    verdicts = [
        registry.run(strategy_id, _CONTEXT) for strategy_id in registry.list_ids()
    ]
    assert [verdict.strategy_id for verdict in verdicts] == [
        "contract_first",
        "contract_second",
        "contract_third",
    ]
    assert [verdict.recommendation for verdict in verdicts] == ["buy", "hold", "sell"]
    assert all(isinstance(verdict, Verdict) for verdict in verdicts)


def test_registry_filters_discovered_plugins_by_lifecycle_state():
    registry = _registry(_FirstAgent(), _SecondAgent(), _ThirdAgent())

    assert [
        manifest["strategy_id"] for manifest in registry.list_manifests("active")
    ] == ["contract_first"]
    assert [
        manifest["strategy_id"] for manifest in registry.list_manifests("experimental")
    ] == ["contract_second"]
    assert [
        manifest["strategy_id"] for manifest in registry.list_manifests("deprecated")
    ] == ["contract_third"]


def test_deprecated_strategy_rejected_from_public_collection_chain():
    """拍板01 语义：deprecated 策略被隔离门拒收（仅回放显式 allow_deprecated 放行），
    收集链落 SKIPPED_ERROR，不进公共收集主链；新增插件仍无需改主链。"""
    registry = _registry(_FirstAgent(), _SecondAgent())
    before, skips_before = run_shadow._collect_verdicts_with_skips(
        registry, _CONTEXT, timeout_s=1.0
    )

    registry.register(_ThirdAgent())
    after, skips_after = run_shadow._collect_verdicts_with_skips(
        registry, _CONTEXT, timeout_s=1.0
    )

    assert [verdict.strategy_id for verdict in before] == [
        "contract_first",
        "contract_second",
    ]
    assert skips_before == []
    # deprecated 拒收：主链 verdicts 不含第三策略，且 registry 直呼仍可达（回放面）
    assert [verdict.strategy_id for verdict in after] == [
        "contract_first",
        "contract_second",
    ]
    assert [verdict.strategy_id for verdict in after] == [
        sid for sid in registry.list_ids() if sid != "contract_third"
    ]
    assert len(skips_after) == 1
    assert skips_after[0]["strategy_id"] == "contract_third"
    assert skips_after[0]["status"] == "SKIPPED_ERROR"
    assert "deprecated" in skips_after[0]["reason"]
