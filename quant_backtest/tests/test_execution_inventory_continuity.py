# -*- coding: utf-8 -*-
"""Regression tests for inventory continuity across GTC and one-shot orders."""
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bar_dto import Bar
from bar_store import BarStore
from execution_engine import ExecutionConfig, InventoryLot, OrderIntent, run_from_store
from fee_schedule import CostCalculator, FeeSchedule, FeeScheduleProvider


SYMBOL = "ABC.SZ"
SESSION = date(2026, 10, 16)
IDENTITY = ("CN", "SZSE", "equity", SYMBOL)


def make_bar(minute: int, price: float = 10.0) -> Bar:
    """Create a closed, observable bar without external market access."""
    moment = datetime(2026, 10, 16, 1, minute, tzinfo=timezone.utc)
    return Bar(SYMBOL, "CN", "1m", moment, SESSION, price, price, price,
               price, 100, price * 100, moment, moment, True,
               "fixture", "v1", "none", "observed")


def make_calculator() -> CostCalculator:
    """Build a deterministic schedule for order-level fee assertions."""
    schedule = FeeSchedule(
        schedule_ref="continuity", version="1", market="CN", exchange="SZSE",
        effective_from=date(2026, 1, 1), effective_until=date(2027, 1, 1),
        commission_rate=Decimal("0.01"), commission_min=Decimal("0"),
        stamp_duty_side_mask=(), rounding=Decimal("0.01"),
    )
    return CostCalculator(FeeScheduleProvider([schedule]))


def assert_inventory(result, quantity: float) -> None:
    """Verify the symbol summary, instrument map, and lots agree."""
    assert result.positions[SYMBOL] == pytest.approx(quantity)
    assert result.instrument_positions == {IDENTITY: pytest.approx(quantity)}
    assert sum(lot.quantity for lot in result.inventory_lots) == pytest.approx(quantity)


@pytest.mark.parametrize("fee_mode", ["PER_FILL", "PER_ORDER"])
def test_zero_fill_initial_positions_preserve_lots_and_marked_equity(fee_mode: str) -> None:
    """No eligible GTC fill must not erase legacy positions from accounting."""
    bars = [make_bar(30), make_bar(31)]
    config = ExecutionConfig(
        initial_cash=100, initial_positions={SYMBOL: 2}, market="CN",
        policy_ref="registry:market-cn", fee_mode=fee_mode,
        fee_calculator=make_calculator() if fee_mode == "PER_ORDER" else None,
    )
    intent = OrderIntent(SYMBOL, "BUY", 1, bars[0].dt,
                         order_id="never-fill", time_in_force="GTC",
                         order_type="LIMIT", limit_price=5)
    result = run_from_store(BarStore(bars), [intent], config,
                            status_provider=lambda *_args: "TRADING")
    assert not result.fills
    assert result.cash == pytest.approx(100)
    assert result.equity == pytest.approx(120)
    assert_inventory(result, 2)
    assert result.inventory_lots[0].lot_id == "initial-1"
    assert result.inventory_lots[0].available_session is None
    assert result.audits[0].status == "unfilled"


def test_zero_fill_one_share_at_twenty_marks_equity_at_120() -> None:
    """An untouched one-share legacy holding contributes the terminal twenty."""
    bars = [make_bar(30, 10), make_bar(31, 20)]
    intent = OrderIntent(SYMBOL, "BUY", 1, bars[0].dt, order_id="no-fill-at-twenty",
                         time_in_force="GTC", order_type="LIMIT", limit_price=5)
    result = run_from_store(
        BarStore(bars), [intent],
        ExecutionConfig(initial_cash=100, initial_positions={SYMBOL: 1},
                        market="CN", policy_ref="registry:market-cn"),
        status_provider=lambda *_args: "TRADING",
    )
    assert result.fills == ()
    assert result.cash == pytest.approx(100)
    assert_inventory(result, 1)
    assert result.equity == pytest.approx(120)
    assert result.inventory_lots[0].available_session is None


def test_gtc_fill_then_day_buy_preserves_inventory_and_cash() -> None:
    """A filled GTC must pass its lots, never both mutually exclusive inputs."""
    bars = [make_bar(30), make_bar(31), make_bar(32)]
    intents = [
        OrderIntent(SYMBOL, "BUY", 1, bars[0].dt, order_id="gtc-buy",
                    time_in_force="GTC", max_attempts=1),
        OrderIntent(SYMBOL, "BUY", 2, bars[1].dt, order_id="day-buy"),
    ]
    result = run_from_store(
        BarStore(bars), intents,
        ExecutionConfig(initial_cash=100, initial_positions={SYMBOL: 2},
                        market="CN", policy_ref="registry:market-cn"),
        status_provider=lambda *_args: "TRADING",
    )
    assert [fill.quantity for fill in result.fills] == [1, 2]
    assert [audit.status for audit in result.audits] == ["filled", "filled"]
    assert result.cash == pytest.approx(70)
    assert result.equity == pytest.approx(120)
    assert_inventory(result, 5)
    assert result.inventory_lots[0].lot_id == "initial-1"
    assert result.inventory_lots[0].quantity == pytest.approx(2)


def test_gtc_without_fill_then_day_sell_keeps_age_unknown() -> None:
    """A subsequent DAY order cannot make an unknown-age legacy lot T+1 eligible."""
    bars = [make_bar(30), make_bar(31), make_bar(32)]
    intents = [
        OrderIntent(SYMBOL, "BUY", 1, bars[0].dt, order_id="unfilled-gtc",
                    time_in_force="GTC", max_attempts=1,
                    order_type="LIMIT", limit_price=5),
        OrderIntent(SYMBOL, "SELL", 2, bars[1].dt, order_id="day-sell"),
    ]
    result = run_from_store(
        BarStore(bars), intents,
        ExecutionConfig(initial_cash=100, initial_positions={SYMBOL: 2},
                        market="CN", policy_ref="registry:market-cn"),
        status_provider=lambda *_args: "TRADING",
    )
    assert result.fills == ()
    assert result.audits[1].reason == "INVENTORY_AGE_UNKNOWN"
    assert result.cash == pytest.approx(100)
    assert result.equity == pytest.approx(120)
    assert_inventory(result, 2)


def test_gtc_fill_then_day_sell_known_lot_respects_t_plus_one() -> None:
    """An already settled CN lot can sell while the fresh GTC lot stays locked."""
    bars = [make_bar(30), make_bar(31), make_bar(32)]
    settled_lot = InventoryLot(SYMBOL, "CN", "SZSE", "equity",
                               date(2026, 10, 15), SESSION, 2, "settled-lot")
    intents = [
        OrderIntent(SYMBOL, "BUY", 1, bars[0].dt, order_id="gtc-buy",
                    time_in_force="GTC", max_attempts=1),
        OrderIntent(SYMBOL, "SELL", 2, bars[1].dt, order_id="day-sell"),
    ]
    result = run_from_store(
        BarStore(bars), intents,
        ExecutionConfig(initial_cash=100, initial_lots=(settled_lot,),
                        market="CN", policy_ref="registry:market-cn"),
        status_provider=lambda *_args: "TRADING",
    )
    assert [fill.quantity for fill in result.fills] == [1, 2]
    assert [audit.status for audit in result.audits] == ["filled", "filled"]
    assert result.cash == pytest.approx(110)
    assert result.equity == pytest.approx(120)
    assert_inventory(result, 1)
    assert result.inventory_lots[0].acquisition_session == SESSION
    assert result.inventory_lots[0].available_session > SESSION


def test_same_day_gtc_buy_then_day_sell_respects_t_plus_one() -> None:
    """A newly purchased lot cannot become available at the DAY handoff."""
    bars = [make_bar(30), make_bar(31), make_bar(32)]
    intents = [
        OrderIntent(SYMBOL, "BUY", 1, bars[0].dt, order_id="gtc-buy",
                    time_in_force="GTC", max_attempts=1),
        OrderIntent(SYMBOL, "SELL", 1, bars[1].dt, order_id="day-sell"),
    ]
    result = run_from_store(
        BarStore(bars), intents,
        ExecutionConfig(initial_cash=100, market="CN",
                        policy_ref="registry:market-cn"),
        status_provider=lambda *_args: "TRADING",
    )
    assert len(result.fills) == 1
    assert result.audits[1].reason == "T_PLUS_ONE"
    assert result.cash == pytest.approx(90)
    assert_inventory(result, 1)
    assert result.inventory_lots[0].available_session > SESSION
    assert result.equity == pytest.approx(100)


def test_per_order_gtc_then_day_charges_fees_once_without_losing_lots() -> None:
    """One fee per accepted order and accurate final inventory survive handoff."""
    bars = [make_bar(30), make_bar(31), make_bar(32)]
    intents = [
        OrderIntent(SYMBOL, "BUY", 1, bars[0].dt, order_id="gtc-buy",
                    time_in_force="GTC", max_attempts=1),
        OrderIntent(SYMBOL, "BUY", 1, bars[1].dt, order_id="day-buy"),
    ]
    result = run_from_store(
        BarStore(bars), intents,
        ExecutionConfig(initial_cash=100, initial_positions={SYMBOL: 2}, market="CN",
                        fee_mode="PER_ORDER", fee_calculator=make_calculator(),
                        policy_ref="registry:market-cn"),
        status_provider=lambda *_args: "TRADING",
    )
    assert len(result.fills) == 2
    assert [fill.fee for fill in result.fills] == pytest.approx([0.1, 0.1])
    assert [fill.fee_mode for fill in result.fills] == ["PER_ORDER", "PER_ORDER"]
    assert result.cash == pytest.approx(79.8)
    assert result.equity == pytest.approx(119.8)
    assert_inventory(result, 4)
    assert len(result.inventory_lots) == 3


def test_explicit_lot_t_plus_one_remains_blocked_after_gtc() -> None:
    """A real dated lot retains settlement metadata through a GTC retry."""
    bars = [make_bar(30), make_bar(31), make_bar(32)]
    future_lot = InventoryLot(SYMBOL, "CN", "SZSE", "equity",
                              SESSION, date(2026, 10, 17), 2, "future-lot")
    intents = [
        OrderIntent(SYMBOL, "BUY", 1, bars[0].dt, order_id="unfilled-gtc",
                    time_in_force="GTC", max_attempts=1,
                    order_type="LIMIT", limit_price=5),
        OrderIntent(SYMBOL, "SELL", 2, bars[1].dt, order_id="day-sell"),
    ]
    result = run_from_store(
        BarStore(bars), intents,
        ExecutionConfig(initial_cash=100, initial_lots=(future_lot,),
                        market="CN", policy_ref="registry:market-cn"),
        status_provider=lambda *_args: "TRADING",
    )
    assert result.fills == ()
    assert result.audits[1].reason == "T_PLUS_ONE"
    assert result.inventory_lots == (future_lot,)
    assert_inventory(result, 2)
    assert result.equity == pytest.approx(120)


def test_per_order_without_schedule_returns_marked_initial_inventory() -> None:
    """Fail-closed fee mode still reports its untouched opening holdings."""
    bars = [make_bar(30), make_bar(31)]
    result = run_from_store(
        BarStore(bars), [OrderIntent(SYMBOL, "BUY", 1, bars[0].dt,
                                     time_in_force="GTC")],
        ExecutionConfig(initial_cash=100, initial_positions={SYMBOL: 2},
                        fee_mode="PER_ORDER", market="CN",
                        policy_ref="registry:market-cn"),
    )
    assert result.fills == ()
    assert result.audits[0].reason == "FEE_CONFIG_UNSUPPORTED"
    assert_inventory(result, 2)
    assert result.equity == pytest.approx(120)
