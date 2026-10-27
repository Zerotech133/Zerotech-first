# -*- coding: utf-8 -*-
"""Tests for immutable, auditable PER_ORDER fee aggregation."""
from decimal import Decimal
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fee_schedule import (  # noqa: E402
    FeeSchedule,
    FeeScheduleError,
    OrderFeeAccumulator,
)


def make_schedule(**overrides):
    values = dict(
        schedule_ref="per-order",
        version="1",
        market="CN",
        exchange="SSE",
        commission_rate=Decimal("0.0001"),
        commission_min=Decimal("5"),
        stamp_duty_rate=Decimal("0.0005"),
        stamp_duty_side_mask=("SELL",),
        transfer_fee_rate=Decimal("0.00001"),
        regulatory_fee_rate=Decimal("0.00002"),
        exchange_fee_rate=Decimal("0.00003"),
        fixed_fee=Decimal("1"),
        rounding=Decimal("0.01"),
    )
    values.update(overrides)
    return FeeSchedule(**values)


def test_three_fills_allocate_order_level_components_once():
    schedule = make_schedule(commission_min=Decimal("0"), fixed_fee=Decimal("2"))
    accumulator = OrderFeeAccumulator(schedule, side="BUY")
    accumulator.add_fill("10", "10")
    accumulator.add_fill("20", "10")
    accumulator.add_fill("30", "10")

    allocations = accumulator.finalize()
    assert len(allocations) == 3
    assert sum((item.quantity for item in allocations), Decimal("0")) == Decimal("60")
    assert sum((item.gross_value for item in allocations), Decimal("0")) == Decimal("600")
    assert sum((item.fixed_fee for item in allocations), Decimal("0")) == Decimal("2.00")
    assert sum((item.commission for item in allocations), Decimal("0")) == Decimal("0.06")
    assert accumulator.total == sum((item.total for item in allocations), Decimal("0"))


def test_minimum_commission_and_fixed_fee_are_charged_once():
    schedule = make_schedule(commission_rate=Decimal("0.0001"), commission_min=Decimal("5"), fixed_fee=Decimal("3"))
    accumulator = OrderFeeAccumulator(schedule, side="BUY")
    accumulator.add_fill(1, 100)
    accumulator.add_fill(1, 100)
    accumulator.add_fill(1, 100)

    allocations = accumulator.finalize()
    assert sum((item.commission for item in allocations), Decimal("0")) == Decimal("5.00")
    assert sum((item.fixed_fee for item in allocations), Decimal("0")) == Decimal("3.00")
    assert allocations[-1].commission == Decimal("0.00")
    assert allocations[-1].fixed_fee == Decimal("0.00")


def test_rounding_remainder_is_absorbed_by_last_fill():
    schedule = make_schedule(
        commission_rate=Decimal("0.001"), commission_min=Decimal("0"),
        stamp_duty_rate=Decimal("0"), transfer_fee_rate=Decimal("0"),
        regulatory_fee_rate=Decimal("0"), exchange_fee_rate=Decimal("0"), fixed_fee=Decimal("0"),
    )
    accumulator = OrderFeeAccumulator(schedule, side="BUY")
    # Each fill rounds to zero, while the aggregate 8.008 commission rounds
    # to 0.01; the last fill must absorb that cumulative remainder.
    accumulator.add_fill(1, Decimal("4.004"))
    accumulator.add_fill(1, Decimal("4.004"))

    allocations = accumulator.finalize()
    assert allocations[0].commission == Decimal("0.00")
    assert allocations[1].commission == Decimal("0.01")
    assert sum((item.commission for item in allocations), Decimal("0")) == Decimal("0.01")
    assert accumulator.aggregate_breakdown().commission == Decimal("0.01")


def test_schedule_identity_and_hash_mismatch_fail_closed():
    schedule = make_schedule()
    accumulator = OrderFeeAccumulator(schedule, side="BUY")
    changed = make_schedule(commission_rate=Decimal("0.0002"))
    with pytest.raises(FeeScheduleError, match="mismatch"):
        accumulator.add_fill(1, 100, schedule=changed)
    with pytest.raises(FeeScheduleError, match="hash mismatch"):
        accumulator.add_fill(1, 100, schedule_hash="not-the-schedule-hash")


def test_empty_order_has_no_fees_or_breakdown():
    accumulator = OrderFeeAccumulator(make_schedule(), side="SELL")
    assert accumulator.finalize() == ()
    assert accumulator.total == Decimal("0")
    assert accumulator.aggregate_breakdown() is None


def test_buy_sell_directional_stamp_and_cash_delta():
    schedule = make_schedule(commission_min=Decimal("0"), fixed_fee=Decimal("0"))
    buy = OrderFeeAccumulator(schedule, side="BUY")
    sell = OrderFeeAccumulator(schedule, side="SELL")
    buy.add_fill(100, 100)
    sell.add_fill(100, 100)

    buy_allocation = buy.finalize()[0]
    sell_allocation = sell.finalize()[0]
    assert buy_allocation.stamp_duty == Decimal("0.00")
    assert sell_allocation.stamp_duty == Decimal("5.00")
    assert buy_allocation.cash_delta == -(buy_allocation.gross_value + buy_allocation.total)
    assert sell_allocation.cash_delta == sell_allocation.gross_value - sell_allocation.total
    assert buy_allocation.schedule_hash == schedule.schedule_hash


def test_side_mismatch_is_rejected():
    accumulator = OrderFeeAccumulator(make_schedule(), side="BUY")
    with pytest.raises(FeeScheduleError, match="side mismatch"):
        accumulator.add_fill(1, 100, side="SELL")
