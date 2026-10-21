# -*- coding: utf-8 -*-
"""Adversarial temporal safety checks for multi-timeframe and multi-order retry."""
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bar_dto import Bar
from bar_store import BarStore
from execution_engine import ExecutionConfig, OrderIntent, _run_from_store_once, run_from_store
from fee_schedule import CostCalculator, FeeSchedule, FeeScheduleProvider


SESSION = date(2026, 10, 16)


def bar(symbol: str, minute: int, timeframe: str = "1m", market: str = "CN") -> Bar:
    """Make a closed observed bar whose price and evidence are immediately visible."""
    moment = datetime(2026, 10, 16, 1, minute, tzinfo=timezone.utc)
    return Bar(symbol, market, timeframe, moment, SESSION, 10.0, 10.0, 10.0,
               10.0, 100.0, 1000.0, moment, moment, True, "fixture", "v1",
               "none", "observed", ("fixture",))


def calculator() -> CostCalculator:
    """Provide a real deterministic fee schedule for PER_ORDER execution."""
    schedule = FeeSchedule(schedule_ref="cn-temporal", version="1", market="CN",
                           exchange="SZSE", effective_from=date(2026, 1, 1),
                           effective_until=date(2027, 1, 1),
                           commission_rate=Decimal("0"),
                           commission_min=Decimal("0"),
                           stamp_duty_side_mask=(), rounding=Decimal("0.01"))
    return CostCalculator(FeeScheduleProvider([schedule]))


@pytest.mark.parametrize("timeframe,expected_minute", [("1m", 31), ("5m", 32)])
def test_gtc_mixed_timeframes_never_execute_another_stream(
    timeframe: str, expected_minute: int
) -> None:
    """A 5m candidate must not drive a 1m retry, or vice versa."""
    signal = bar("ABC.SZ", 30, timeframe)
    different = bar("ABC.SZ", 31 if timeframe == "5m" else 32,
                    "1m" if timeframe == "5m" else "5m")
    matching = bar("ABC.SZ", expected_minute, timeframe)
    order = OrderIntent("ABC.SZ", "BUY", 1, signal.dt, market="CN",
                        timeframe=timeframe, time_in_force="GTC", order_id="mixed")
    result = run_from_store(BarStore([signal, different, matching]), [order],
                            ExecutionConfig(initial_cash=100, market="CN",
                                            policy_ref="registry:market-cn"),
                            status_provider=lambda *_args: "TRADING")
    assert [fill.dt for fill in result.fills] == [matching.dt]
    assert result.audits[0].query_metadata["attempts"] == 1
    assert result.cash == pytest.approx(90)


def test_explicit_retry_candidate_missing_fails_closed_without_signal_fallback() -> None:
    """A selected wrong-timeframe bar cannot regress execution to the signal."""
    signal = bar("ABC.SZ", 30, "1m")
    wrong_stream = bar("ABC.SZ", 31, "5m")
    order = OrderIntent("ABC.SZ", "BUY", 1, signal.dt, market="CN",
                        timeframe="1m", time_in_force="GTC")
    result = _run_from_store_once(
        BarStore([signal, wrong_stream]), [order],
        ExecutionConfig(initial_cash=100, market="CN", policy_ref="registry:market-cn",
                        next_bar=False),
        _execution_signal_at=signal.dt, _execution_submitted_at=signal.dt,
        _execution_bar_dt=wrong_stream.dt,
        status_provider=lambda *_args: "TRADING")
    assert result.fills == () and result.cash == 100
    assert result.audits[0].reason == "NO_NEXT_BAR"


def test_gtc_cannot_trade_before_delayed_submission() -> None:
    """Retry bars older than order submission cannot receive a fill."""
    signal, early, late = (bar("ABC.SZ", minute) for minute in (30, 31, 33))
    submitted = bar("ABC.SZ", 32).dt
    order = OrderIntent("ABC.SZ", "BUY", 1, submitted, signal_at=signal.dt,
                        market="CN", timeframe="1m", time_in_force="GTC")
    result = run_from_store(BarStore([signal, early, late]), [order],
                            ExecutionConfig(initial_cash=100, market="CN",
                                            policy_ref="registry:market-cn"),
                            status_provider=lambda *_args: "TRADING")
    assert [fill.dt for fill in result.fills] == [late.dt]
    assert result.audits[0].query_metadata["attempts"] == 1


def test_gtc_missing_candidate_does_not_fall_back_to_signal() -> None:
    """A filtered-out retry bar cannot trigger a fill at the signal timestamp."""
    signal = bar("ABC.SZ", 30, "1m")
    wrong_stream = bar("ABC.SZ", 31, "5m")
    order = OrderIntent("ABC.SZ", "BUY", 1, signal.dt, market="CN",
                        timeframe="1m", time_in_force="GTC", order_id="missing")
    result = run_from_store(BarStore([signal, wrong_stream]), [order],
                            ExecutionConfig(initial_cash=100, market="CN",
                                            policy_ref="registry:market-cn"),
                            status_provider=lambda *_args: "TRADING")
    assert result.fills == ()
    assert result.cash == 100
    assert result.audits[0].reason == "ORDER_EXPIRED"
    assert result.audits[0].query_metadata["attempts"] == 0


@pytest.mark.parametrize("mode", ["PER_FILL", "PER_ORDER"])
def test_crossed_order_windows_fail_closed_before_cash_or_inventory_changes(mode: str) -> None:
    """A 14:33 SELL cannot fund the 14:32 BUY in sequential GTC processing."""
    abc_signal, abc_sell = bar("ABC.SZ", 30), bar("ABC.SZ", 33)
    def_signal, def_buy = bar("DEF.SZ", 31), bar("DEF.SZ", 32)
    orders = [OrderIntent("ABC.SZ", "SELL", 1, abc_signal.dt, market="CN",
                          time_in_force="GTC", order_id="sell", max_attempts=1),
              OrderIntent("DEF.SZ", "BUY", 1, def_signal.dt, market="CN",
                          time_in_force="GTC", order_id="buy", max_attempts=1)]
    config = ExecutionConfig(initial_cash=0, initial_positions={"ABC.SZ": 1},
                             market="CN", fee_mode=mode,
                             fee_calculator=calculator() if mode == "PER_ORDER" else None,
                             policy_ref="registry:market-cn")
    result = run_from_store(BarStore([abc_signal, abc_sell, def_signal, def_buy]),
                            orders, config, status_provider=lambda *_args: "TRADING")
    assert result.fills == ()
    assert result.cash == 0 and result.positions == {"ABC.SZ": 1}
    assert len(result.inventory_lots) == 1 and result.inventory_lots[0].quantity == 1
    assert [audit.reason for audit in result.audits] == ["CROSS_ORDER_TEMPORAL_CONFLICT"] * 2
    assert all(audit.query_metadata["error_code"] == "CROSS_ORDER_TEMPORAL_CONFLICT"
               and audit.fill_at is None for audit in result.audits)
    assert result.metrics["filled"] == 0


def test_gtc_max_attempts_only_shortens_window_after_actual_attempt() -> None:
    """A one-attempt order at 14:33 still crosses a later 14:32 order."""
    first_signal, first_candidate = bar("ABC.SZ", 30), bar("ABC.SZ", 33)
    second_signal, second_candidate = bar("DEF.SZ", 31), bar("DEF.SZ", 32)
    orders = [OrderIntent("ABC.SZ", "SELL", 1, first_signal.dt, market="CN",
                          time_in_force="GTC", max_attempts=1),
              OrderIntent("DEF.SZ", "BUY", 1, second_signal.dt, market="CN",
                          time_in_force="GTC", max_attempts=1)]
    result = run_from_store(BarStore([first_signal, first_candidate,
                                      second_signal, second_candidate]), orders,
                            ExecutionConfig(initial_cash=0, initial_positions={"ABC.SZ": 1},
                                            market="CN", policy_ref="registry:market-cn"))
    assert result.cash == 0 and result.positions == {"ABC.SZ": 1}
    assert all(audit.reason == "CROSS_ORDER_TEMPORAL_CONFLICT" for audit in result.audits)


def test_possible_later_gtc_retry_blocks_even_when_earlier_attempt_would_fill() -> None:
    """Do not use observed early completion to declare a crossing order safe."""
    abc_signal, abc_first, abc_later = (bar("ABC.SZ", minute) for minute in (30, 31, 34))
    def_signal, def_candidate = bar("DEF.SZ", 32), bar("DEF.SZ", 33)
    orders = [OrderIntent("ABC.SZ", "BUY", 1, abc_signal.dt, market="CN",
                          time_in_force="GTC", order_id="first"),
              OrderIntent("DEF.SZ", "BUY", 1, def_signal.dt, market="CN",
                          time_in_force="GTC", order_id="second")]
    result = run_from_store(BarStore([abc_signal, abc_first, abc_later,
                                      def_signal, def_candidate]), orders,
                            ExecutionConfig(initial_cash=100, market="CN",
                                            policy_ref="registry:market-cn"),
                            status_provider=lambda *_args: "TRADING")
    assert not result.fills
    assert result.cash == 100 and result.positions == {}
    assert all(audit.reason == "CROSS_ORDER_TEMPORAL_CONFLICT" for audit in result.audits)


@pytest.mark.parametrize("mode", ["PER_FILL", "PER_ORDER"])
def test_non_crossing_and_single_gtc_orders_still_execute(mode: str) -> None:
    """Safe ordered batches and a single GTC preserve existing fill behavior."""
    first_signal, first_candidate = bar("ABC.SZ", 30), bar("ABC.SZ", 31)
    second_signal, second_candidate = bar("DEF.SZ", 32), bar("DEF.SZ", 33)
    first = OrderIntent("ABC.SZ", "BUY", 1, first_signal.dt, market="CN",
                        time_in_force="GTC", order_id="first", max_attempts=1)
    second = OrderIntent("DEF.SZ", "BUY", 1, second_signal.dt, market="CN",
                         time_in_force="GTC", order_id="second", max_attempts=1)
    config = ExecutionConfig(initial_cash=100, market="CN", fee_mode=mode,
                             fee_calculator=calculator() if mode == "PER_ORDER" else None,
                             policy_ref="registry:market-cn")
    store = BarStore([first_signal, first_candidate, second_signal, second_candidate])
    for orders in ([first], [first, second]):
        result = run_from_store(store, orders, config,
                                status_provider=lambda *_args: "TRADING")
        assert [fill.dt for fill in result.fills] == ([first_candidate.dt]
                                                        if len(orders) == 1 else
                                                        [first_candidate.dt, second_candidate.dt])
        assert result.cash == pytest.approx(100 - len(orders) * 10)
        assert [audit.status for audit in result.audits] == ["filled"] * len(orders)
