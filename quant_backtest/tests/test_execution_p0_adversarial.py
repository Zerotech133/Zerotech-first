# -*- coding: utf-8 -*-
"""Isolated adversarial checks for execution chronology, ownership and marks."""
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bar_dto import Bar, BarQuery
from bar_store import BarStore
from execution_engine import ExecutionConfig, InventoryLot, OrderIntent, run_from_store
from fill_model import VolumeParticipationFillModel
from fee_schedule import CostCalculator, FeeSchedule, FeeScheduleProvider


def bar(symbol: str, minute: int, price: float = 10.0, volume: float = 100.0) -> Bar:
    """Create one immediately observable UTC minute bar."""
    at = datetime(2026, 10, 16, 1, minute, tzinfo=timezone.utc)
    return Bar(symbol, "CN", "1m", at, date(2026, 10, 16), price, price,
               price, price, volume, price * volume, at, at, True,
               "synthetic", "1", "none", "observed")


def policy(**overrides: object) -> ExecutionConfig:
    """Build an isolated simulation policy without production dependencies."""
    return ExecutionConfig(initial_cash=100, market="CN", next_bar=True,
                           t_plus_one=True, policy_ref="registry:market-cn", **overrides)


def test_day_later_sell_does_not_fund_earlier_buy() -> None:
    """DAY/PER_FILL must not credit 01:33 cash to a 01:32 attempt."""
    bars = [bar("A.SZ", 30), bar("A.SZ", 33), bar("B.SZ", 31), bar("B.SZ", 32)]
    orders = [OrderIntent("A.SZ", "SELL", 1, bars[0].dt, market="CN"),
              OrderIntent("B.SZ", "BUY", 1, bars[2].dt, market="CN")]
    result = run_from_store(BarStore(bars), orders,
                            ExecutionConfig(initial_cash=0,
                                            initial_lots=(InventoryLot("A.SZ", "CN", "SZSE",
                                                                       "equity", date(2026, 10, 15),
                                                                       date(2026, 10, 16), 1, "owned"),),
                                            market="CN", t_plus_one=True,
                                            policy_ref="registry:market-cn"),
                            status_provider=lambda *_args: "TRADING")
    assert not result.fills
    assert [audit.reason for audit in result.audits] == ["CROSS_ORDER_TEMPORAL_CONFLICT"] * 2
    assert result.cash == 0


def test_sell_cannot_borrow_same_symbol_from_other_exchange() -> None:
    """The complete instrument identity, not display symbol, owns a lot."""
    opening = InventoryLot("ABC.SZ", "CN", "SSE", "equity", None,
                           date(2026, 10, 16), 1, "foreign-exchange")
    signal, execution = bar("ABC.SZ", 30), bar("ABC.SZ", 31)
    order = OrderIntent("ABC.SZ", "SELL", 1, signal.dt, market="CN",
                        exchange="SZSE")
    # Isolate the sell gate; equity valuation is independently tested below.
    with patch("execution_engine._value_inventory", return_value=(110.0, {})):
        result = run_from_store(BarStore([signal, execution]), [order],
                                policy(initial_lots=(opening,)),
                                status_provider=lambda *_args: "TRADING")
    assert not result.fills
    assert result.cash == 100
    assert result.audits[0].reason == "INSUFFICIENT_POSITION"
    assert result.inventory_lots == (opening,)


def test_mark_uses_visible_history_outside_execution_filter() -> None:
    """A query narrowed to a different symbol must retain existing-lot marks."""
    opening = InventoryLot("A.SZ", "CN", "SZSE", "equity", None, None, 1, "held")
    observed = bar("A.SZ", 30, 20)
    filtered = bar("B.SZ", 31, 10)
    result = run_from_store(BarStore([observed, filtered]), [],
                            policy(initial_lots=(opening,)),
                            query=BarQuery(start=filtered.dt, end=filtered.dt,
                                           as_of=filtered.dt))
    assert result.cash == 100
    assert result.equity == pytest.approx(120)


def test_missing_or_future_only_mark_fails_closed() -> None:
    """Neither a missing mark nor a post-as-of mark is worth zero."""
    opening = InventoryLot("A.SZ", "CN", "SZSE", "equity", None, None, 1, "held")
    signal = bar("B.SZ", 30)
    late = bar("A.SZ", 33, 20)
    for bars in ([signal], [signal, late]):
        with pytest.raises(ValueError, match="VALUATION_PRICE_UNAVAILABLE"):
            run_from_store(BarStore(bars), [], policy(initial_lots=(opening,)),
                           query=BarQuery(as_of=signal.dt))


@pytest.mark.parametrize("tif", ["DAY", "GTC"])
def test_same_bar_volume_budget_shared_across_day_orders(tif: str) -> None:
    """Two 100-share orders cannot both consume one 100-share bar."""
    signal, execution = bar("A.SZ", 30), bar("A.SZ", 31)
    orders = [OrderIntent("A.SZ", "BUY", 100, signal.dt, market="CN", order_id=name,
                          time_in_force=tif, max_attempts=1 if tif == "GTC" else None)
              for name in ("one", "two")]
    result = run_from_store(BarStore([signal, execution]), orders,
                            ExecutionConfig(initial_cash=10000, market="CN",
                                            t_plus_one=True,
                                            policy_ref="registry:market-cn",
                                            fill_model=VolumeParticipationFillModel(1.0)),
                            status_provider=lambda *_args: "TRADING")
    assert sum(fill.quantity for fill in result.fills) == pytest.approx(100), result.audits
    assert result.cash == pytest.approx(9000)
    assert [audit.status for audit in result.audits] == ["filled", "unfilled"]
    assert result.audits[1].reason in {"NO_LIQUIDITY", "ORDER_EXPIRED"}


def test_rejected_order_does_not_consume_shared_liquidity() -> None:
    """A cash-rejected order leaves bar volume for a later accepted order."""
    signal, execution = bar("A.SZ", 30), bar("A.SZ", 31)
    rejected = OrderIntent("A.SZ", "BUY", 100, signal.dt, market="CN", order_id="one")
    accepted = OrderIntent("A.SZ", "BUY", 10, signal.dt, market="CN", order_id="two")
    result = run_from_store(BarStore([signal, execution]), [rejected, accepted],
                            ExecutionConfig(initial_cash=100, market="CN", t_plus_one=True,
                                            policy_ref="registry:market-cn",
                                            fill_model=VolumeParticipationFillModel(1.0)),
                            status_provider=lambda *_args: "TRADING")
    assert [audit.reason for audit in result.audits] == ["INSUFFICIENT_CASH", "FILLED"]
    assert [fill.quantity for fill in result.fills] == [10]
    assert result.cash == pytest.approx(0)


def test_per_order_volume_budget_is_shared_and_fees_conserved() -> None:
    """Order-level fee reconciliation cannot mint additional bar liquidity."""
    signal, execution = bar("A.SZ", 30), bar("A.SZ", 31)
    schedule = FeeSchedule(schedule_ref="synthetic", version="1", market="CN",
                           exchange="SZSE", effective_from=date(2026, 1, 1),
                           effective_until=date(2027, 1, 1),
                           commission_rate=Decimal("0.001"),
                           commission_min=Decimal("0.01"),
                           stamp_duty_side_mask=(), rounding=Decimal("0.01"))
    orders = [OrderIntent("A.SZ", "BUY", 100, signal.dt, market="CN", order_id=name,
                          time_in_force="GTC", max_attempts=1)
              for name in ("one", "two")]
    result = run_from_store(BarStore([signal, execution]), orders,
                            ExecutionConfig(initial_cash=10000, market="CN", t_plus_one=True,
                                            fee_mode="PER_ORDER",
                                            fee_calculator=CostCalculator(FeeScheduleProvider([schedule])),
                                            fill_model=VolumeParticipationFillModel(1.0),
                                            policy_ref="registry:market-cn"),
                            status_provider=lambda *_args: "TRADING")
    assert [fill.quantity for fill in result.fills] == [100]
    assert result.cash == pytest.approx(10000 - result.fills[0].gross_value - result.fills[0].fee)
    assert result.audits[1].status == "unfilled"
