"""P0-3 ExecutionOrchestrator SELL T+1 守卫测试（隔离，无真实 DB/不下单）。

覆盖：
- SELL 提交边界先过统一守卫；sim/live 共用同一判定（一次校验，两侧都下/都不下）；
- 注入守卫：放行 / 抛异常 / 返回拒绝决策 三种路径；
- 默认 A 股守卫（t1_guard）：CN 当日开仓拒绝、T+1 可卖放行、opened_at 缺失拒绝、
  无持仓/可卖量不足拒绝、无行情（停牌）拒绝；
- 非 CN T+0（US）不受 A 股守卫影响（保持既有行为）；
- BUY 路径不触发卖出守卫。
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from zhice_os_v2.application.services.approval_center import StrategySwitch
from zhice_os_v2.application.services.execution_orchestrator import (
    ExecutionOrchestrator,
    SellGuardRejected,
)
from zhice_os_v2.domain.execution.broker import (
    ExecutionMode,
    OrderResult,
    OrderSide,
    OrderStatus,
)
from zhice_os_v2.domain.execution.t1_guard import SellDecision
from zhice_os_v2.domain.market.context import MarketContext
from zhice_os_v2.domain.market.registry import get_profile
from zhice_os_v2.domain.ontology.objects import utc_now
from zhice_os_v2.domain.portfolio.position import PositionLot
from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase

CN_CODE = "600519.SH"
US_CODE = "AAPL"
SV = "sv-t1"


class _FakeBroker:
    """记录 place_order 的假券商（返回 FILLED）。"""

    def __init__(self, name: str, events: list[str]) -> None:
        self.name = name
        self.events = events
        self.requests = []

    def place_order(self, ctx, request):  # noqa: ANN001, ANN201 - 测试桩
        self.events.append(self.name)
        self.requests.append(request)
        return OrderResult(
            order_id=request.order_id,
            status=OrderStatus.FILLED,
            filled_qty=request.qty,
            filled_price=request.limit_price,
        )


class _DurableOutbox:
    supports_durable_outbox = True


def _ctx(market: str = "CN") -> MarketContext:
    profile = get_profile(market)
    return MarketContext(
        market=market,
        profile=profile,
        currency=profile.currency,
        tz=profile.tz,
    )


def _switch(market: str = "CN", *, sim: bool = True, live: bool = False) -> StrategySwitch:
    return StrategySwitch(
        market=market,
        strategy_version_id=SV,
        simulated_enabled=sim,
        live_enabled=live,
    )


def _position(
    opened_at,
    *,
    qty: str = "100",
    market: str = "CN",
    ts_code: str = CN_CODE,
    position_id: str = "pos-t1-orch",
) -> PositionLot:
    return PositionLot(
        position_id=position_id,
        strategy_version_id=SV,
        market=market,
        ts_code=ts_code,
        direction="BUY",
        open_qty=Decimal(qty),
        open_price=Decimal("10"),
        opened_at=opened_at,
    )


def _orchestrator(
    *,
    events: list[str],
    switches: dict,
    sell_guard=None,
    live: bool = False,
) -> tuple[ExecutionOrchestrator, _FakeBroker, _FakeBroker | None]:
    sim = _FakeBroker("sim", events)
    brokers = {ExecutionMode.SIMULATED: sim}
    live_broker = None
    if live:
        live_broker = _FakeBroker("live", events)
        brokers[ExecutionMode.LIVE] = live_broker
    orch = ExecutionOrchestrator(
        brokers,
        switches,
        cancellation_outbox=_DurableOutbox(),
        sell_guard=sell_guard,
    )
    return orch, sim, live_broker


def _execute_sell(orch: ExecutionOrchestrator, *, market: str = "CN",
                  ts_code: str = CN_CODE) -> list:
    return orch.execute(
        _ctx(market),
        strategy_version_id=SV,
        order_intent_id="oi-t1",
        ts_code=ts_code,
        side=OrderSide.SELL,
        qty=Decimal("100"),
        price=Decimal("10"),
        user_strategy_id=None,
    )


# --------------------------------------------------------------------------- #
# 注入守卫：sim/live 共用一次判定                                               #
# --------------------------------------------------------------------------- #


def test_injected_guard_allows_once_and_both_modes_submit() -> None:
    """守卫放行：只调用一次，模拟盘+实盘都按同一卖点提交。"""
    events: list[str] = []
    guard_calls: list[tuple] = []

    def guard(ctx, ts_code, qty, user_strategy_id):
        events.append("guard")
        guard_calls.append((ctx.market, ts_code, qty, user_strategy_id))
        return None

    orch, sim, live_broker = _orchestrator(
        events=events,
        switches={("CN", SV): _switch(sim=True, live=True)},
        sell_guard=guard,
        live=True,
    )

    results = _execute_sell(orch)

    assert len(guard_calls) == 1, "sim/live 应共用一次守卫判定"
    assert events == ["guard", "sim", "live"], events
    assert sim.requests and live_broker is not None and live_broker.requests
    assert len(results) == 2
    # 同一卖点：两路 OrderRequest 的标的/方向/数量/价格完全一致
    a, b = sim.requests[0], live_broker.requests[0]
    assert (a.ts_code, a.side, a.qty, a.limit_price) == (
        b.ts_code, b.side, b.qty, b.limit_price
    )
    assert str(a.side.value).upper() == "SELL"


def test_injected_guard_exception_blocks_both_modes() -> None:
    """守卫抛异常：不提交任何券商订单（含 sim）。"""
    events: list[str] = []

    def guard(ctx, ts_code, qty, user_strategy_id):
        raise RuntimeError("guard denied")

    orch, sim, live_broker = _orchestrator(
        events=events,
        switches={("CN", SV): _switch(sim=True, live=True)},
        sell_guard=guard,
        live=True,
    )

    with pytest.raises(RuntimeError, match="guard denied"):
        _execute_sell(orch)

    assert sim.requests == []
    assert live_broker is not None and live_broker.requests == []


def test_injected_guard_deny_decision_raises_sell_guard_rejected() -> None:
    """守卫返回 ok=False：抛 SellGuardRejected（带原因/证据），两侧都不下单。"""
    events: list[str] = []

    def guard(ctx, ts_code, qty, user_strategy_id):
        return SellDecision(
            ok=False,
            reason="T+1锁：CN 当日开仓不可卖（可卖量 0 < 请求 100）",
            market="CN",
            requested_qty=Decimal("100"),
            available_qty=Decimal("0"),
            locked=True,
        )

    orch, sim, _ = _orchestrator(
        events=events,
        switches={("CN", SV): _switch(sim=True)},
        sell_guard=guard,
    )

    with pytest.raises(SellGuardRejected) as ei:
        _execute_sell(orch)

    assert ei.value.code == "sell.t1_blocked"
    assert "T+1" in ei.value.reason
    assert ei.value.details["locked"] is True
    assert sim.requests == []


def test_injected_guard_dict_denied_raises_with_code() -> None:
    """兼容 ExecutionService.sell_guard 的 dict 返回：denied=True → 拒绝且带 code。"""
    events: list[str] = []

    def guard(ctx, ts_code, qty, user_strategy_id):
        return {
            "denied": True,
            "deny_code": "sell.t1_locked",
            "deny_reason": "T+1：当日开仓批次当日不可卖",
            "sellable_qty": "0",
        }

    orch, sim, _ = _orchestrator(
        events=events,
        switches={("CN", SV): _switch(sim=True)},
        sell_guard=guard,
    )

    with pytest.raises(SellGuardRejected) as ei:
        _execute_sell(orch)

    assert ei.value.code == "sell.t1_locked"
    assert "T+1" in ei.value.reason
    assert sim.requests == []


def test_buy_does_not_call_sell_guard() -> None:
    """BUY 路径不触发卖出守卫（开仓门禁逻辑保持独立）。"""
    events: list[str] = []

    def guard(ctx, ts_code, qty, user_strategy_id):
        raise AssertionError("BUY 不应调用 SELL 守卫")

    orch, sim, _ = _orchestrator(
        events=events,
        switches={("CN", SV): _switch(sim=True)},
        sell_guard=guard,
    )
    results = orch.execute(
        _ctx("CN"),
        strategy_version_id=SV,
        order_intent_id="oi-buy",
        ts_code=CN_CODE,
        side=OrderSide.BUY,
        qty=Decimal("100"),
        price=Decimal("10"),
    )
    assert len(results) == 1 and len(sim.requests) == 1


# --------------------------------------------------------------------------- #
# 默认 A 股守卫（t1_guard + 隔离 DB/行情打桩）                                  #
# --------------------------------------------------------------------------- #


def _patch_default_guard_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    db: InMemoryDatabase,
    prices: dict,
    pre_closes: dict,
) -> None:
    import zhice_os_v2.api.dependencies as deps_mod
    import zhice_os_v2.api.routers.positions as pos_mod

    monkeypatch.setattr(deps_mod, "get_db", lambda: db)
    monkeypatch.setattr(pos_mod, "_get_latest_prices", lambda symbols, market: dict(prices))
    monkeypatch.setattr(pos_mod, "_get_pre_closes", lambda symbols, market: dict(pre_closes))


def test_default_guard_blocks_cn_same_day_position(monkeypatch) -> None:
    """默认守卫：CN 当日开仓 → SellGuardRejected，券商不下单。"""
    db = InMemoryDatabase()
    db.save_position(_position(utc_now()))
    _patch_default_guard_env(
        monkeypatch, db=db,
        prices={CN_CODE: 10.0}, pre_closes={CN_CODE: 10.0},
    )
    events: list[str] = []
    orch, sim, _ = _orchestrator(
        events=events, switches={("CN", SV): _switch(sim=True)}
    )

    with pytest.raises(SellGuardRejected) as ei:
        _execute_sell(orch)

    assert "T+1" in ei.value.reason
    assert sim.requests == []


def test_default_guard_allows_cn_yesterday_position(monkeypatch) -> None:
    """默认守卫：T+1 可卖（昨仓）+ 有效行情 → 放行提交。"""
    db = InMemoryDatabase()
    db.save_position(_position(utc_now() - timedelta(days=1)))
    _patch_default_guard_env(
        monkeypatch, db=db,
        prices={CN_CODE: 10.0}, pre_closes={CN_CODE: 10.0},
    )
    events: list[str] = []
    orch, sim, _ = _orchestrator(
        events=events, switches={("CN", SV): _switch(sim=True)}
    )

    results = _execute_sell(orch)

    assert len(results) == 1 and len(sim.requests) == 1


def test_default_guard_blocks_missing_opened_at(monkeypatch) -> None:
    """默认守卫：opened_at 缺失 fail-closed。"""
    db = InMemoryDatabase()
    db.save_position(_position(None))
    _patch_default_guard_env(
        monkeypatch, db=db,
        prices={CN_CODE: 10.0}, pre_closes={CN_CODE: 10.0},
    )
    events: list[str] = []
    orch, sim, _ = _orchestrator(
        events=events, switches={("CN", SV): _switch(sim=True)}
    )

    with pytest.raises(SellGuardRejected) as ei:
        _execute_sell(orch)

    assert "opened_at" in ei.value.reason
    assert sim.requests == []


def test_default_guard_blocks_no_position(monkeypatch) -> None:
    """默认守卫：无可卖持仓（可卖量不足）→ 拒绝。"""
    db = InMemoryDatabase()
    _patch_default_guard_env(
        monkeypatch, db=db,
        prices={CN_CODE: 10.0}, pre_closes={CN_CODE: 10.0},
    )
    events: list[str] = []
    orch, sim, _ = _orchestrator(
        events=events, switches={("CN", SV): _switch(sim=True)}
    )

    with pytest.raises(SellGuardRejected) as ei:
        _execute_sell(orch)

    assert "可卖量不足" in ei.value.reason
    assert sim.requests == []


def test_default_guard_blocks_missing_quote(monkeypatch) -> None:
    """默认守卫：无行情（停牌/数据缺失）→ 拒绝。"""
    db = InMemoryDatabase()
    db.save_position(_position(utc_now() - timedelta(days=1)))
    _patch_default_guard_env(monkeypatch, db=db, prices={}, pre_closes={})
    events: list[str] = []
    orch, sim, _ = _orchestrator(
        events=events, switches={("CN", SV): _switch(sim=True)}
    )

    with pytest.raises(SellGuardRejected) as ei:
        _execute_sell(orch)

    assert "无有效卖价" in ei.value.reason
    assert sim.requests == []


def test_default_guard_skips_non_cn_t0_market(monkeypatch) -> None:
    """默认守卫只针对 A 股：US T+0 当日仓不因 T+1 被拦（保持既有行为）。"""
    events: list[str] = []
    orch, sim, _ = _orchestrator(
        events=events, switches={("US", SV): _switch(market="US", sim=True)}
    )

    results = _execute_sell(orch, market="US", ts_code=US_CODE)

    assert len(results) == 1 and len(sim.requests) == 1
