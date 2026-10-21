# -*- coding: utf-8 -*-
"""Acceptance tests for the versioned Decimal fee schedule contract."""
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fee_schedule import (  # noqa: E402
    CostCalculator,
    CostBreakdown,
    FeeSchedule,
    FeeScheduleError,
    FeeScheduleProvider,
    LegacyFlatFeeSchedule,
)


UTC = timezone.utc


def schedule(**overrides):
    values = dict(
        schedule_ref="cn-a",
        version="2026.01",
        market="CN",
        exchange="SSE",
        asset_class="equity",
        currency="CNY",
        effective_from=date(2026, 1, 1),
        effective_until=date(2027, 1, 1),
        commission_rate=Decimal("0.00025"),
        commission_min=Decimal("5"),
        stamp_duty_rate=Decimal("0.0005"),
        transfer_fee_rate=Decimal("0.00001"),
        regulatory_fee_rate=Decimal("0.00002"),
        exchange_fee_rate=Decimal("0.00003"),
        fixed_fee=Decimal("0"),
    )
    values.update(overrides)
    return FeeSchedule(**values)


def calculator(**overrides):
    return CostCalculator(FeeScheduleProvider([schedule(**overrides)]))


def test_effective_intervals_are_half_open_and_adjacent_versions_resolve():
    first = schedule(effective_from=date(2026, 1, 1), effective_until=date(2026, 7, 1))
    second = schedule(
        schedule_ref="cn-b",
        version="2026.02",
        effective_from=date(2026, 7, 1),
        effective_until=date(2027, 1, 1),
    )
    provider = FeeScheduleProvider([first, second])

    assert first.applies_at(datetime(2026, 6, 30, 23, 59, tzinfo=UTC))
    assert not first.applies_at(date(2026, 7, 1))
    assert provider.resolve(market="CN", exchange="SSE", at=date(2026, 7, 1)) == second


def test_overlapping_identity_intervals_are_rejected_but_different_currency_isolated():
    provider = FeeScheduleProvider([schedule()])
    with pytest.raises(FeeScheduleError, match="overlapping"):
        provider.add(schedule(schedule_ref="cn-overlap", version="2026.02"))

    provider.add(schedule(schedule_ref="cn-usd", currency="USD"))
    assert len(provider.schedules) == 2


def test_missing_identity_or_time_fails_closed():
    provider = FeeScheduleProvider([schedule()])
    with pytest.raises(FeeScheduleError, match="missing"):
        provider.resolve(market="CN", exchange="SZSE", at=date(2026, 2, 1))
    with pytest.raises(FeeScheduleError, match="missing"):
        provider.resolve(market="CN", exchange="SSE", currency="USD", at=date(2026, 2, 1))
    with pytest.raises(FeeScheduleError, match="missing"):
        provider.resolve(market="CN", exchange="SSE", at=date(2027, 1, 1))


def test_sell_stamp_duty_and_component_rounding_are_directional():
    calc = calculator()
    buy = calc.calculate(side="BUY", quantity="100", price="100", at=date(2026, 3, 1), market="CN", exchange="SSE")
    sell = calc.calculate(side="SELL", quantity="100", price="100", at=date(2026, 3, 1), market="CN", exchange="SSE")

    assert buy.gross_value == Decimal("10000")
    assert buy.schedule_hash == schedule().schedule_hash
    assert len(buy.schedule_hash) == 64
    assert all(character in "0123456789abcdef" for character in buy.schedule_hash)
    assert buy.stamp_duty == Decimal("0")
    assert sell.stamp_duty == Decimal("5.00")
    assert buy.commission == Decimal("5.00")
    assert sell.total == sum((sell.commission, sell.stamp_duty, sell.transfer_fee,
                              sell.regulatory_fee, sell.exchange_fee, sell.fixed_fee), Decimal("0"))


def test_minimum_commission_applies_before_rounding():
    calc = calculator(commission_rate=Decimal("0.0001"), commission_min=Decimal("5"))
    result = calc.calculate(side="BUY", quantity=1, price=100, at=date(2026, 2, 1), market="CN", exchange="SSE")
    assert result.commission == Decimal("5.00")


def test_buy_and_sell_cash_delta_conserve_gross_plus_costs():
    calc = calculator()
    for side in ("BUY", "SELL"):
        result = calc.calculate(side=side, quantity=100, price=100, at=date(2026, 3, 1), market="CN", exchange="SSE")
        assert result.total == sum((result.commission, result.stamp_duty, result.transfer_fee,
                                    result.regulatory_fee, result.exchange_fee, result.fixed_fee), Decimal("0"))
        expected = -(result.gross_value + result.total) if side == "BUY" else result.gross_value - result.total
        assert result.cash_delta == expected


def test_zero_negative_and_nonfinite_inputs_are_rejected():
    with pytest.raises(FeeScheduleError):
        calculator().calculate(side="BUY", quantity=0, price=100, at=date(2026, 2, 1), market="CN", exchange="SSE")
    with pytest.raises(FeeScheduleError):
        calculator().calculate(side="BUY", quantity=1, price="NaN", at=date(2026, 2, 1), market="CN", exchange="SSE")
    with pytest.raises(FeeScheduleError):
        FeeScheduleProvider([schedule(commission_rate=Decimal("-0.1"))])
    with pytest.raises(FeeScheduleError):
        FeeScheduleProvider([schedule(rounding=Decimal("0"))])


def test_invalid_schedule_interval_and_side_are_rejected():
    with pytest.raises(FeeScheduleError, match="effective_from"):
        FeeScheduleProvider([schedule(effective_from=date(2027, 1, 1), effective_until=date(2026, 1, 1))])
    with pytest.raises(FeeScheduleError, match="side"):
        calculator().calculate(side="HOLD", quantity=1, price=100, at=date(2026, 2, 1), market="CN", exchange="SSE")
    with pytest.raises(FeeScheduleError):
        FeeScheduleProvider([schedule(stamp_duty_side_mask=("HOLD",))])


def test_cost_breakdown_rejects_nonconserving_manual_values():
    kwargs = dict(
        schedule_ref="x", version="1", market="CN", exchange="SSE", asset_class="equity", currency="CNY",
        side="BUY", quantity=Decimal("1"), price=Decimal("10"), gross_value=Decimal("10"),
        commission=Decimal("1"), stamp_duty=Decimal("0"), transfer_fee=Decimal("0"),
        regulatory_fee=Decimal("0"), exchange_fee=Decimal("0"), fixed_fee=Decimal("0"),
        total=Decimal("0"), cash_delta=Decimal("-11"),
    )
    with pytest.raises(FeeScheduleError, match="total"):
        CostBreakdown(**kwargs)


def test_cost_breakdown_rejects_negative_components():
    kwargs = dict(
        schedule_ref="x", version="1", market="CN", exchange="SSE", asset_class="equity", currency="CNY",
        side="BUY", quantity=Decimal("1"), price=Decimal("10"), gross_value=Decimal("10"),
        commission=Decimal("-1"), stamp_duty=Decimal("0"), transfer_fee=Decimal("0"),
        regulatory_fee=Decimal("0"), exchange_fee=Decimal("0"), fixed_fee=Decimal("0"),
        total=Decimal("-1"), cash_delta=Decimal("-9"),
    )
    with pytest.raises(FeeScheduleError, match="non-negative"):
        CostBreakdown(**kwargs)


def test_legacy_adapter_has_one_flat_commission_and_no_stamp():
    legacy = LegacyFlatFeeSchedule(fee_rate=Decimal("0.001"), market="legacy", exchange="legacy")
    converted = legacy.to_schedule()
    calc = CostCalculator(FeeScheduleProvider([converted]))
    result = calc.calculate(side="SELL", quantity=100, price=10, at=date(2026, 1, 1), market="legacy", exchange="legacy")

    assert result.commission == Decimal("1.00")
    assert result.stamp_duty == Decimal("0.00")
    assert result.total == result.commission
    assert result.cash_delta == Decimal("999.00")
