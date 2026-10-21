# -*- coding: utf-8 -*-
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bar_dto import Bar
from bar_store import BarStore
from execution_engine import ExecutionConfig, OrderIntent, VolumeParticipationFillModel, run_from_store
from fee_schedule import CostCalculator, FeeSchedule, FeeScheduleProvider


def make_bar(minute, price=10, volume=100):
    dt = datetime(2026, 10, 16, 1, minute, tzinfo=timezone.utc)
    return Bar("000001.SZ", "CN", "1m", dt, date(2026, 10, 16), price, price, price,
               price, volume, price * volume, dt, dt, True, "fixture", "v1", "none", "observed")


def calculator(commission_rate="0.001", commission_min="0"):
    schedule = FeeSchedule(
        schedule_ref="cn-test", version="1", market="CN", exchange="SZSE",
        effective_from=date(2026, 1, 1), effective_until=date(2027, 1, 1),
        commission_rate=Decimal(commission_rate), commission_min=Decimal(commission_min),
        stamp_duty_rate=Decimal("0"), stamp_duty_side_mask=(),
    )
    return CostCalculator(FeeScheduleProvider([schedule]))


def run(intent, config, bars=None):
    bars = bars or [make_bar(30), make_bar(31, 11)]
    return run_from_store(BarStore(bars), [intent], config, status_provider=lambda *args: "TRADING")


def test_per_fill_uses_final_quantity_and_price_and_audits_breakdown():
    signal, execution = make_bar(30), make_bar(31, 11)
    intent = OrderIntent("000001.SZ", "BUY", 100, signal.dt, market="CN")
    result = run(intent, ExecutionConfig(initial_cash=2000, slippage_bps=100,
                                         fee_calculator=calculator(), policy_ref="registry:market-cn"),
                 [signal, execution])
    fill, audit = result.fills[0], result.audits[0]
    assert fill.price == pytest.approx(11.11)
    assert fill.gross_value == pytest.approx(1111)
    assert fill.fee == pytest.approx(1.11)
    assert result.cash == pytest.approx(2000 - 1111 - 1.11)
    assert fill.fee_mode == audit.fee_mode == "PER_FILL"
    assert fill.fee_source == audit.fee_source == "fee_schedule"
    assert fill.fee_schedule_identity == audit.fee_schedule_identity == "cn-test@1"
    assert fill.fee_schedule_hash == audit.fee_schedule_hash == result.audits[0].query_metadata["fee_breakdown"].get("schedule_hash")
    assert len(fill.fee_schedule_hash) == 64
    assert all(character.isdigit() or character in "abcdef" for character in fill.fee_schedule_hash)
    assert fill.fee_breakdown["total"] == "1.11"
    assert audit.query_metadata["fee_breakdown"]["cash_delta"] == "-1112.11"


def test_partial_fill_charges_once_for_accepted_quantity():
    signal, execution = make_bar(30), make_bar(31, 11, volume=50)
    calls = []
    base = calculator()

    class CountingCalculator:
        def calculate(self, **kwargs):
            calls.append(kwargs)
            return base.calculate(**kwargs)

    intent = OrderIntent("000001.SZ", "BUY", 100, signal.dt, market="CN")
    result = run(intent, ExecutionConfig(initial_cash=2000, lot_size=10,
                                         fill_model=VolumeParticipationFillModel(.5, 10),
                                         fee_calculator=CountingCalculator(), policy_ref="registry:market-cn"),
                 [signal, execution])
    assert len(calls) == 1
    assert calls[0]["quantity"] == 20
    assert len(result.fills) == 1 and result.fills[0].quantity == 20
    assert result.audits[0].status == "partial"


def test_per_order_hook_aggregates_fee_and_preserves_schedule_metadata():
    signal, execution = make_bar(30), make_bar(31)
    result = run(OrderIntent("000001.SZ", "BUY", 100, signal.dt, market="CN"),
                 ExecutionConfig(initial_cash=2000, fee_calculator=calculator(), fee_mode="PER_ORDER",
                                 policy_ref="registry:market-cn"), [signal, execution])
    assert result.fills and result.audits[0].reason == "FILLED"
    assert result.fills[0].fee_mode == result.audits[0].fee_mode == "PER_ORDER"
    assert result.fills[0].fee_schedule_identity == result.audits[0].fee_schedule_identity == "cn-test@1"
    assert result.fills[0].fee_schedule_hash == result.audits[0].fee_schedule_hash
    assert result.fills[0].fee == pytest.approx(1.00)
    assert result.cash == pytest.approx(2000 - 1000 - 1.00)


def test_fee_failure_fails_closed_without_legacy_fallback():
    signal, execution = make_bar(30), make_bar(31)
    intent = OrderIntent("000001.SZ", "BUY", 100, signal.dt, market="CN")
    result = run(intent, ExecutionConfig(initial_cash=2000, fee_provider=FeeScheduleProvider(),
                                         policy_ref="registry:market-cn"), [signal, execution])
    assert result.audits[0].reason == "FEE_SCHEDULE_UNAVAILABLE"
    assert not result.fills and result.cash == 2000 and result.positions == {}
