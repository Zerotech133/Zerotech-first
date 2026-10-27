# -*- coding: utf-8 -*-
"""Agent Verdict 到 quant_backtest OrderIntent 的隔离测试。"""

from dataclasses import replace
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import importlib
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PROJECT_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(REPO_ROOT))

from agent_platform.contracts.sim_market import SimMarketBar
from agent_platform.engine.strategy_registry import Verdict
from bar_dto import Bar
from bar_store import BarStore
from execution_engine import ExecutionConfig, OrderIntent
from strategy_layer.adapters.agent_sim_market import (
    AgentSimMarketAdapter,
    AgentSimMarketError,
    SimMarketStatusEvidence,
    simulate_verdict,
    sim_market_bar_to_bar,
    static_trading_status_fixture,
    verdict_to_order_intent,
)


WHEN = datetime(2026, 10, 16, 1, 30, tzinfo=timezone.utc)


def _verdict(recommendation, *, symbol="000001.SZ"):
    return Verdict(
        strategy_id="sim_agent",
        symbol=symbol,
        recommendation=recommendation,
        confidence=0.8,
        score=80.0,
        rationale="测试判例",
    )


def _bar(minute, open_price=10.0, close_price=10.0):
    dt = datetime(2026, 10, 16, 1, minute, tzinfo=timezone.utc)
    return Bar(
        "000001.SZ",
        "CN",
        "1m",
        dt,
        date(2026, 10, 16),
        open_price,
        open_price,
        open_price,
        close_price,
        100,
        100,
        dt,
        dt,
        True,
        "fixture",
        "v1",
        "none",
        "observed",
        ("fixture",),
    )


@pytest.mark.parametrize(
    ("recommendation", "side"),
    [("strong_buy", "BUY"), ("buy", "BUY"), ("sell", "SELL"), ("strong_sell", "SELL")],
)
def test_legal_verdicts_map_to_quant_backtest_order_intent(recommendation, side):
    intent = verdict_to_order_intent(
        _verdict(recommendation),
        quantity=123,
        submitted_at=WHEN,
        market="CN",
        timeframe="1m",
    )

    assert isinstance(intent, OrderIntent)
    assert intent.side == side
    assert intent.quantity == 123
    assert intent.submitted_at == WHEN
    assert intent.market == "CN"
    assert intent.timeframe == "1m"


def test_hold_does_not_generate_order():
    assert (
        verdict_to_order_intent(
            _verdict("hold"),
            quantity=100,
            submitted_at=WHEN,
            market="CN",
            timeframe="1m",
        )
        is None
    )


def test_invalid_verdict_is_rejected_before_mapping():
    invalid = _verdict("not-a-recommendation")
    with pytest.raises(AgentSimMarketError, match="Verdict 非法"):
        verdict_to_order_intent(
            invalid,
            quantity=100,
            submitted_at=WHEN,
            market="CN",
            timeframe="1m",
        )

    with pytest.raises(TypeError, match="Verdict"):
        verdict_to_order_intent(
            object(),
            quantity=100,
            submitted_at=WHEN,
            market="CN",
            timeframe="1m",
        )


def test_adapter_keeps_order_fields_explicit():
    adapter = AgentSimMarketAdapter(
        quantity=50,
        submitted_at=WHEN,
        market="CN",
        timeframe="1m",
        order_id="verdict-order-1",
    )
    intent = adapter.to_order_intent(_verdict("buy"))

    assert intent is not None
    assert intent.order_id == "verdict-order-1"
    assert intent.quantity == 50
    assert intent.submitted_at == WHEN
    assert intent.market == "CN"
    assert intent.timeframe == "1m"


def test_simulate_verdict_uses_only_in_memory_store_and_returns_execution_result():
    result = simulate_verdict(
        _verdict("buy"),
        BarStore([_bar(30), _bar(31, open_price=11.0, close_price=12.0)]),
        quantity=100,
        submitted_at=WHEN,
        market="CN",
        timeframe="1m",
        config=ExecutionConfig(
            initial_cash=2000,
            market="CN",
            timeframe="1m",
            policy_ref="registry:market-cn",
        ),
        status_provider=static_trading_status_fixture,
        allow_demo_fixture=True,
    )

    assert len(result.orders) == 1
    assert isinstance(result.orders[0], OrderIntent)
    assert result.orders[0].side == "BUY"
    assert len(result.fills) == 1


def test_simulate_hold_passes_empty_order_sequence():
    result = simulate_verdict(
        _verdict("hold"),
        [_bar(30)],
        quantity=100,
        submitted_at=WHEN,
        market="CN",
        timeframe="1m",
        status_evidence={},
        as_of=WHEN,
    )
    assert result.orders == ()
    assert result.fills == ()


def test_import_does_not_modify_sys_path_and_shares_engine_class_identity():
    before = sys.path[:]
    adapter_module = importlib.import_module("strategy_layer.adapters.agent_sim_market")
    spec = importlib.util.spec_from_file_location(
        "_test_agent_sim_market_import", adapter_module.__file__
    )
    isolated = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = isolated
    try:
        spec.loader.exec_module(isolated)
    finally:
        del sys.modules[spec.name]
    assert sys.path == before
    import execution_engine
    import bar_store
    import bar_dto

    assert isolated.Bar is adapter_module.Bar is execution_engine.Bar is bar_dto.Bar
    assert (
        isolated.BarStore
        is adapter_module.BarStore
        is execution_engine.BarStore
        is bar_store.BarStore
    )
    assert adapter_module.OrderIntent is execution_engine.OrderIntent
    assert adapter_module.Verdict is Verdict
    converted = adapter_module.sim_market_bar_to_bar(
        _sim_bar(),
        market="CN",
        timeframe="1m",
        session_date=WHEN.date(),
        source="fixture",
        source_version="v1",
        calendar_ref="calendar",
        calendar_version="v1",
        close_at=WHEN,
        as_of=WHEN,
    )
    assert isinstance(converted, bar_dto.Bar)
    assert bar_store.BarStore([converted]).all() == (converted,)
    assert isinstance(
        adapter_module.verdict_to_order_intent(
            _verdict("buy"),
            quantity=100,
            submitted_at=WHEN,
            market="CN",
            timeframe="1m",
        ),
        execution_engine.OrderIntent,
    )


def _sim_bar(**kwargs):
    values = dict(
        symbol="000001.SZ",
        timestamp=WHEN.isoformat(),
        open=10,
        high=12,
        low=9,
        close=11,
        volume=100,
        visible_at=WHEN.isoformat(),
    )
    values.update(kwargs)
    return SimMarketBar(**values)


def _convert(bar=None, **kwargs):
    values = dict(
        market="CN",
        timeframe="1m",
        session_date=WHEN.date(),
        source="fixture",
        source_version="v1",
        calendar_ref="calendar",
        calendar_version="v1",
        close_at=WHEN,
        as_of=WHEN,
    )
    values.update(kwargs)
    return sim_market_bar_to_bar(bar or _sim_bar(), **values)


def test_explicit_conversion_preserves_ohlcv_and_visibility_evidence():
    result = _convert()
    assert (result.o, result.h, result.l, result.c, result.v, result.amount) == (
        10,
        12,
        9,
        11,
        100,
        None,
    )
    assert result.dt == result.close_at == result.available_at == WHEN
    assert (result.source, result.source_version) == ("fixture", "v1")
    assert result.source_trace == ("fixture",)
    assert (result.calendar_ref, result.calendar_version) == ("calendar", "v1")
    assert result.adjustment == "none" and result.closed


@pytest.mark.parametrize(
    "bar,overrides,reason",
    [
        (_sim_bar(adjustment="qfq"), {}, "adjustment"),
        (_sim_bar(visible_at=None), {}, "visible_at"),
        (_sim_bar(closed=False), {}, "已收盘"),
        (_sim_bar(visible=False), {}, "可见"),
        (_sim_bar(visible_at=(WHEN + timedelta(minutes=1)).isoformat()), {}, "as_of"),
        (_sim_bar(), {"close_at": WHEN + timedelta(minutes=1)}, "as_of"),
        (_sim_bar(), {"as_of": WHEN - timedelta(minutes=1)}, "as_of"),
    ],
)
def test_conversion_rejects_unusable_bars(bar, overrides, reason):
    with pytest.raises(AgentSimMarketError, match=reason):
        _convert(bar, **overrides)


def test_conversion_rejects_future_as_of_and_invalid_time_order():
    with pytest.raises(AgentSimMarketError, match="未来"):
        _convert(as_of=datetime.now(timezone.utc) + timedelta(days=1))
    with pytest.raises(AgentSimMarketError, match="时间顺序"):
        _convert(close_at=WHEN - timedelta(minutes=1))
    with pytest.raises(AgentSimMarketError, match="时间顺序"):
        _convert(
            _sim_bar(visible_at=WHEN.isoformat()),
            close_at=WHEN + timedelta(seconds=1),
            as_of=WHEN + timedelta(seconds=1),
        )


def test_conversion_requires_explicit_identity_and_time():
    with pytest.raises(TypeError, match="market"):
        sim_market_bar_to_bar(_sim_bar())
    with pytest.raises(AgentSimMarketError, match="source_version"):
        _convert(source_version="")
    with pytest.raises(AgentSimMarketError, match="session_date"):
        _convert(session_date=None)
    with pytest.raises(AgentSimMarketError, match="session_date"):
        _convert(session_date=WHEN.date() + timedelta(days=3))


@pytest.mark.parametrize(
    "name,value",
    [
        ("status_provider", lambda *args: "TRADING"),
        ("limit_provider", lambda *args: "OPEN"),
        ("calendar_provider", object()),
        ("market_rule_provider", object()),
    ],
)
def test_simulate_rejects_arbitrary_providers(name, value):
    with pytest.raises(AgentSimMarketError, match=name):
        simulate_verdict(
            _verdict("buy"),
            [_bar(30)],
            quantity=1,
            submitted_at=WHEN,
            market="CN",
            timeframe="1m",
            status_evidence={},
            as_of=WHEN,
            **{name: value},
        )


@pytest.mark.parametrize("name", ["fill_model", "fee_calculator", "fee_provider"])
def test_simulate_rejects_configured_side_effect_hooks(name):
    with pytest.raises(AgentSimMarketError, match=name):
        simulate_verdict(
            _verdict("buy"),
            [_bar(30)],
            quantity=1,
            submitted_at=WHEN,
            market="CN",
            timeframe="1m",
            config=ExecutionConfig(**{name: object()}),
            status_evidence={},
            as_of=WHEN,
        )


@pytest.mark.parametrize("name", ["calendar_required", "market_rules_required"])
def test_simulate_rejects_config_that_requires_external_providers(name):
    with pytest.raises(AgentSimMarketError, match="外部"):
        simulate_verdict(
            _verdict("buy"),
            [_bar(30)],
            quantity=1,
            submitted_at=WHEN,
            market="CN",
            timeframe="1m",
            config=ExecutionConfig(**{name: True}),
            status_evidence={},
            as_of=WHEN,
        )


def _status(status="TRADING", **overrides):
    values = dict(
        symbol="000001.SZ",
        market="CN",
        session_date=WHEN.date(),
        status=status,
        observed_at=WHEN,
        effective_until=WHEN + timedelta(minutes=3),
        source="fixture-status",
        source_version="v1",
    )
    values.update(overrides)
    return SimMarketStatusEvidence(**values)


def test_offline_status_evidence_allows_historical_in_memory_fill():
    result = simulate_verdict(
        _verdict("buy"),
        [_bar(30), _bar(31, 11, 12)],
        quantity=100,
        submitted_at=WHEN,
        market="CN",
        timeframe="1m",
        config=ExecutionConfig(
            initial_cash=2000,
            market="CN",
            timeframe="1m",
            policy_ref="registry:market-cn",
        ),
        status_evidence={("000001.SZ", WHEN.date()): _status()},
        as_of=WHEN + timedelta(minutes=2),
    )
    assert len(result.fills) == 1


def test_status_evidence_does_not_override_halt_or_unknown():
    common = dict(
        quantity=100,
        submitted_at=WHEN,
        market="CN",
        timeframe="1m",
        config=ExecutionConfig(initial_cash=2000, policy_ref="registry:market-cn"),
        as_of=WHEN + timedelta(minutes=2),
    )
    halted = simulate_verdict(
        _verdict("buy"),
        [_bar(30), _bar(31)],
        status_evidence={("000001.SZ", WHEN.date()): _status("HALTED")},
        **common,
    )
    missing = simulate_verdict(
        _verdict("buy"), [_bar(30), _bar(31)], status_evidence={}, **common
    )
    assert halted.fills == () and missing.fills == ()


def test_status_evidence_rejects_future_and_mismatched_identity():
    common = dict(
        quantity=100,
        submitted_at=WHEN,
        market="CN",
        timeframe="1m",
        as_of=WHEN + timedelta(minutes=2),
    )
    with pytest.raises(AgentSimMarketError, match="晚于 as_of"):
        simulate_verdict(
            _verdict("buy"),
            [_bar(30)],
            status_evidence={
                ("000001.SZ", WHEN.date()): _status(
                    observed_at=WHEN + timedelta(minutes=3),
                    effective_until=WHEN + timedelta(minutes=4),
                )
            },
            **common,
        )
    with pytest.raises(AgentSimMarketError, match="订单市场"):
        simulate_verdict(
            _verdict("buy"),
            [_bar(30)],
            status_evidence={("000001.SZ", WHEN.date()): _status(market="US")},
            **common,
        )
    with pytest.raises(AgentSimMarketError, match="证据键"):
        simulate_verdict(
            _verdict("buy"),
            [_bar(30)],
            status_evidence={("000002.SZ", WHEN.date()): _status()},
            **common,
        )
    with pytest.raises(AgentSimMarketError, match="订单标的"):
        simulate_verdict(
            _verdict("buy"),
            [_bar(30)],
            status_evidence={("000002.SZ", WHEN.date()): _status(symbol="000002.SZ")},
            **common,
        )
    with pytest.raises(AgentSimMarketError, match="独立"):
        simulate_verdict(
            _verdict("buy"),
            [_bar(30)],
            status_evidence={},
            status_provider=static_trading_status_fixture,
            **common,
        )


def test_status_evidence_rejects_bars_after_as_of():
    with pytest.raises(AgentSimMarketError, match="可见性"):
        simulate_verdict(
            _verdict("buy"),
            [_bar(30), _bar(31)],
            quantity=100,
            submitted_at=WHEN,
            market="CN",
            timeframe="1m",
            status_evidence={("000001.SZ", WHEN.date()): _status()},
            as_of=WHEN,
        )


def test_status_evidence_expires_and_cannot_authorize_later_fill():
    result = simulate_verdict(
        _verdict("buy"),
        [_bar(30), _bar(31)],
        quantity=100,
        submitted_at=WHEN,
        market="CN",
        timeframe="1m",
        config=ExecutionConfig(initial_cash=2000, policy_ref="registry:market-cn"),
        status_evidence={
            ("000001.SZ", WHEN.date()): _status(
                effective_until=WHEN + timedelta(seconds=30)
            )
        },
        as_of=WHEN + timedelta(minutes=2),
    )
    assert result.fills == ()


def test_status_evidence_observed_after_execution_cannot_authorize_fill():
    result = simulate_verdict(
        _verdict("buy"),
        [_bar(30), _bar(31)],
        quantity=100,
        submitted_at=WHEN,
        market="CN",
        timeframe="1m",
        config=ExecutionConfig(initial_cash=2000, policy_ref="registry:market-cn"),
        status_evidence={
            ("000001.SZ", WHEN.date()): _status(
                observed_at=WHEN + timedelta(seconds=90)
            )
        },
        as_of=WHEN + timedelta(minutes=2),
    )
    assert result.fills == ()


def test_status_evidence_rejects_invalid_interval():
    with pytest.raises(AgentSimMarketError, match="effective_until"):
        _status(effective_until=WHEN)


def test_status_evidence_rejects_adjusted_or_time_inconsistent_canonical_bars():
    common = dict(
        quantity=100,
        submitted_at=WHEN,
        market="CN",
        timeframe="1m",
        status_evidence={("000001.SZ", WHEN.date()): _status()},
        as_of=WHEN + timedelta(minutes=2),
    )
    with pytest.raises(AgentSimMarketError, match="复权"):
        simulate_verdict(
            _verdict("buy"), [replace(_bar(30), adjustment="qfq")], **common
        )
    with pytest.raises(AgentSimMarketError, match="可见性"):
        simulate_verdict(
            _verdict("buy"), [replace(_bar(30), available_at=None)], **common
        )
    with pytest.raises(AgentSimMarketError, match="可见性"):
        simulate_verdict(
            _verdict("buy"),
            [replace(_bar(30), available_at=WHEN - timedelta(seconds=1))],
            **common,
        )
    with pytest.raises(AgentSimMarketError, match="可见性"):
        simulate_verdict(
            _verdict("buy"),
            [replace(_bar(30), session_date=WHEN.date() - timedelta(days=4))],
            **common,
        )
    with pytest.raises(AgentSimMarketError, match="signal_at"):
        simulate_verdict(
            _verdict("buy"), [_bar(30)], signal_at=WHEN + timedelta(minutes=3), **common
        )


def test_missing_status_evidence_or_implicit_demo_fixture_is_rejected():
    common = dict(quantity=100, submitted_at=WHEN, market="CN", timeframe="1m")
    with pytest.raises(AgentSimMarketError, match="缺少状态证据"):
        simulate_verdict(_verdict("buy"), [_bar(30), _bar(31)], **common)
    with pytest.raises(AgentSimMarketError, match="缺少状态证据"):
        simulate_verdict(_verdict("hold"), [_bar(30)], **common)
    with pytest.raises(AgentSimMarketError, match="显式启用"):
        simulate_verdict(
            _verdict("buy"),
            [_bar(30), _bar(31)],
            status_provider=static_trading_status_fixture,
            **common,
        )
    with pytest.raises(AgentSimMarketError, match="缺少状态证据"):
        simulate_verdict(
            _verdict("buy"), [_bar(30), _bar(31)], allow_demo_fixture=True, **common
        )
    with pytest.raises(AgentSimMarketError, match="演示模式"):
        simulate_verdict(
            _verdict("buy"),
            [_bar(30), _bar(31)],
            status_evidence={},
            as_of=WHEN + timedelta(minutes=2),
            allow_demo_fixture=True,
            **common,
        )


def test_status_evidence_cannot_authorize_bar_unavailable_at_execution():
    delayed = replace(_bar(31), available_at=WHEN + timedelta(minutes=2))
    result = simulate_verdict(
        _verdict("buy"),
        [_bar(30), delayed],
        quantity=100,
        submitted_at=WHEN,
        market="CN",
        timeframe="1m",
        config=ExecutionConfig(initial_cash=2000, policy_ref="registry:market-cn"),
        status_evidence={("000001.SZ", WHEN.date()): _status()},
        as_of=WHEN + timedelta(minutes=2),
    )
    assert result.fills == ()


def test_status_evidence_rejects_order_submitted_after_as_of():
    with pytest.raises(AgentSimMarketError, match="submitted_at"):
        simulate_verdict(
            _verdict("buy"),
            [_bar(30)],
            quantity=100,
            submitted_at=WHEN + timedelta(minutes=3),
            market="CN",
            timeframe="1m",
            status_evidence={},
            as_of=WHEN + timedelta(minutes=2),
        )


def test_status_evidence_rejects_future_as_of():
    with pytest.raises(AgentSimMarketError, match="未来"):
        simulate_verdict(
            _verdict("buy"),
            [_bar(30)],
            quantity=100,
            submitted_at=WHEN,
            market="CN",
            timeframe="1m",
            status_evidence={},
            as_of=datetime.now(timezone.utc) + timedelta(days=1),
        )


def test_source_label_alone_does_not_authorize_trading():
    result = simulate_verdict(
        _verdict("buy"),
        [
            replace(_bar(30), source="trusted-official"),
            replace(_bar(31), source="trusted-official"),
        ],
        quantity=100,
        submitted_at=WHEN,
        market="CN",
        timeframe="1m",
        config=ExecutionConfig(initial_cash=2000, policy_ref="registry:market-cn"),
        status_evidence={},
        as_of=WHEN + timedelta(minutes=2),
    )
    assert result.fills == ()
