# -*- coding: utf-8 -*-
"""strategy 第十一域骨架测试（K-ORG-167）。

覆盖：StrategyAgent 插件接口 / Verdict 出口校验（枚举白名单+越界拒绝）/
StrategyRegistry 注册幂等（同键异值=漂移拒绝）/ run 出口统一把守。
"""
import sys
from pathlib import Path

import pytest

PLATFORM = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLATFORM / "engine"))

from strategy_registry import (  # noqa: E402
    RECOMMENDATIONS, STANCES, StrategyAgent, StrategyError,
    StrategyRegistry, Verdict)
from strategy_config import RunAuthorization  # noqa: E402


def _v(**kw):
    base = dict(strategy_id="s1", symbol="600519", recommendation="hold",
                confidence=0.6, stance="bull")
    base.update(kw)
    return Verdict(**base)


class _Fixed(StrategyAgent):
    def __init__(self, sid, stance="bull", verdict=None):
        self.strategy_id = sid
        self.stance = stance
        self._verdict = verdict

    def propose(self, context):
        return self._verdict


# ---------------------------------------------------------------- Verdict

def test_verdict_valid_passes():
    assert _v().validate() is not None


def test_recommendation_whitelist():
    with pytest.raises(StrategyError, match="五档枚举"):
        _v(recommendation="all_in").validate()
    assert len(RECOMMENDATIONS) == 5


def test_confidence_bounds():
    with pytest.raises(StrategyError, match="越界"):
        _v(confidence=1.5).validate()
    with pytest.raises(StrategyError, match="越界"):
        _v(confidence=-0.1).validate()


def test_stance_enum():
    with pytest.raises(StrategyError, match="stance"):
        _v(stance="bullish").validate()
    assert set(STANCES) == {"bull", "bear", "neutral"}


def test_symbol_required():
    with pytest.raises(StrategyError, match="symbol"):
        _v(symbol="").validate()


# ---------------------------------------------------------------- Registry

def test_register_and_get():
    reg = StrategyRegistry()
    sid = reg.register(_Fixed("trend_1", "bull"))
    assert sid == "trend_1" and reg.get("trend_1") is reg._strategies["trend_1"]


def test_duplicate_register_rejected():
    reg = StrategyRegistry()
    reg.register(_Fixed("dup"))
    with pytest.raises(StrategyError, match="重复注册"):
        reg.register(_Fixed("dup"))


def test_register_requires_strategy_agent():
    with pytest.raises(StrategyError, match="StrategyAgent"):
        StrategyRegistry().register(object())


def test_register_requires_id_and_valid_stance():
    with pytest.raises(StrategyError, match="strategy_id"):
        StrategyRegistry().register(_Fixed(""))
    with pytest.raises(StrategyError, match="stance"):
        StrategyRegistry().register(_Fixed("x", stance="long"))


def test_list_ids_and_stance_filter():
    reg = StrategyRegistry()
    reg.register(_Fixed("b_bear", "bear"))
    reg.register(_Fixed("a_bull", "bull"))
    reg.register(_Fixed("c_bull", "bull"))
    assert reg.list_ids() == ["a_bull", "b_bear", "c_bull"]
    assert reg.list_ids(stance="bull") == ["a_bull", "c_bull"]
    with pytest.raises(StrategyError, match="未注册"):
        reg.get("ghost")


# ---------------------------------------------------------------- run 出口

def test_run_validates_output():
    reg = StrategyRegistry()
    reg.register(_Fixed("s_ok", verdict=_v(strategy_id="s_ok")))
    v = reg.run("s_ok", {"symbol": "600519"})
    assert v.recommendation == "hold"


def test_run_rejects_non_verdict():
    class _Bad(StrategyAgent):
        strategy_id = "s_bad"
        stance = "bull"

        def propose(self, context):
            return {"recommendation": "hold"}  # 裸 dict 逃逸企图

    reg = StrategyRegistry()
    reg.register(_Bad())
    with pytest.raises(StrategyError, match="非 Verdict"):
        reg.run("s_bad", {})


def test_run_rejects_id_mismatch():
    reg = StrategyRegistry()
    reg.register(_Fixed("s_x", verdict=_v(strategy_id="s_other")))
    with pytest.raises(StrategyError, match="不符"):
        reg.run("s_x", {})


def test_run_rejects_invalid_enum_from_plugin():
    reg = StrategyRegistry()
    reg.register(_Fixed("s_e", verdict=_v(strategy_id="s_e", recommendation="yolo")))
    with pytest.raises(StrategyError, match="五档枚举"):
        reg.run("s_e", {})


def test_base_class_propose_unimplemented():
    with pytest.raises(NotImplementedError):
        StrategyAgent().propose({})


def test_registry_binds_and_returns_authorization_snapshot():
    class AuthorizedAgent(StrategyAgent):
        strategy_id = "authorized"
        version = "1.2.3"

    authorization = RunAuthorization(
        strategy_id="authorized",
        version="1.2.3",
        mode="SHADOW",
        context_bytes=128,
        resource_budget={
            "timeout_s": 1.0,
            "max_concurrency": 1,
            "max_context_bytes": 1024,
        },
        allowed_modes=frozenset({"SHADOW"}),
        permissions=frozenset({"propose", "read_context"}),
        allowed_data=("bars",),
    )
    registry = StrategyRegistry()
    registry.register(AuthorizedAgent())
    bound = registry.bind_authorization(authorization)
    assert bound.as_dict() == authorization.as_dict()
    assert registry.authorization("authorized").mode == "SHADOW"


def test_registry_rejects_unsafe_authorization_permissions():
    with pytest.raises(ValueError, match="propose、read_context"):
        RunAuthorization(
            strategy_id="unsafe",
            version="1.0.0",
            mode="SHADOW",
            context_bytes=1,
            resource_budget={"timeout_s": 1, "max_concurrency": 1,
                             "max_context_bytes": 10},
            allowed_modes=frozenset({"SHADOW"}),
            permissions=frozenset({"propose", "order"}),
            allowed_data=("bars",),
        )
