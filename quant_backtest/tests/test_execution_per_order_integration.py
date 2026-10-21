# -*- coding: utf-8 -*-
"""Integration coverage for PER_ORDER GTC retries and fee conservation."""
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bar_dto import Bar
from bar_store import BarStore
from execution_engine import ExecutionConfig, OrderIntent, run_from_store
from fill_model import VolumeParticipationFillModel
from fee_schedule import CostCalculator, FeeSchedule, FeeScheduleProvider


SYMBOL = "000001.SZ"


def make_bar(minute: int, price: float = 10.0, volume: float = 0.0) -> Bar:
    moment = datetime(2026, 10, 16, 1, minute, tzinfo=timezone.utc)
    return Bar(SYMBOL, "CN", "1m", moment, date(2026, 10, 16), price, price, price,
               price, volume, price * volume, moment, moment, True, "fixture", "v1", "none", "observed")


def make_calculator(*, commission_min: str = "0.01") -> CostCalculator:
    schedule = FeeSchedule(schedule_ref="cn-gtc", version="1", market="CN", exchange="SZSE",
                            effective_from=date(2026, 1, 1), effective_until=date(2027, 1, 1),
                            commission_rate=Decimal("0.001"), commission_min=Decimal(commission_min),
                            stamp_duty_side_mask=(), rounding=Decimal("0.01"))
    return CostCalculator(FeeScheduleProvider([schedule]))


def test_three_bar_gtc_retries_partial_fills_and_conserves_order_fee():
    bars = [make_bar(30, volume=0), make_bar(31, 11, volume=40), make_bar(32, 12, volume=80)]
    signal = bars[0]
    intent = OrderIntent(SYMBOL, "BUY", 100, signal.dt, market="CN", order_id="gtc-1",
                         time_in_force="GTC", max_attempts=3)
    result = run_from_store(
        BarStore(bars), [intent],
        ExecutionConfig(initial_cash=5000, lot_size=10, fee_mode="PER_ORDER",
                        fee_calculator=make_calculator(),
                        fill_model=VolumeParticipationFillModel(1.0, 10),
                        policy_ref="registry:market-cn"),
        status_provider=lambda *_args: "TRADING",
    )
    assert [fill.quantity for fill in result.fills] == [40, 60]
    assert len(result.audits) == 1
    audit = result.audits[0]
    assert audit.status == "filled"
    assert audit.query_metadata["attempts"] == 2
    assert audit.query_metadata["retry_reasons"] == ["PARTIAL_FILL", "FILLED"]
    assert audit.query_metadata["fee_schedule_identity"] == "cn-gtc@1"
    assert sum(fill.fee for fill in result.fills) == pytest.approx(
        float(Decimal(audit.query_metadata["aggregate_fee_breakdown"]["total"]))
    )
    assert result.cash == pytest.approx(5000 - sum(fill.gross_value for fill in result.fills) - sum(fill.fee for fill in result.fills))
    assert result.metrics["filled"] == 2
    assert result.metrics["fully_filled_count"] == 1


def test_gtc_visibility_uses_original_signal_and_submission_gate():
    bars = [make_bar(30, volume=0), make_bar(31, volume=100)]
    intent = OrderIntent(SYMBOL, "BUY", 10, bars[0].dt, market="CN",
                         time_in_force="GTC", max_attempts=2)
    result = run_from_store(BarStore(bars), [intent], ExecutionConfig(
        initial_cash=1000, fee_mode="PER_ORDER", fee_calculator=make_calculator(),
        fill_model=VolumeParticipationFillModel(1.0, 10), policy_ref="registry:market-cn"),
        status_provider=lambda *_args: "TRADING")
    assert result.fills and result.fills[0].dt == bars[1].dt
    assert result.audits[0].submitted_at == intent.submitted_at


def test_per_order_without_provider_fails_closed():
    bars = [make_bar(30, volume=0), make_bar(31, volume=100)]
    intent = OrderIntent(SYMBOL, "BUY", 10, bars[0].dt, market="CN", time_in_force="GTC")
    result = run_from_store(BarStore(bars), [intent], ExecutionConfig(
        initial_cash=1000, fee_mode="PER_ORDER", policy_ref="registry:market-cn"))
    assert not result.fills
    assert len(result.audits) == 1 and result.audits[0].reason == "FEE_CONFIG_UNSUPPORTED"
    assert result.cash == 1000 and result.positions == {}


def test_zero_fill_has_one_summary_audit_and_retry_trace():
    bars = [make_bar(30, volume=0), make_bar(31, volume=0), make_bar(32, volume=0)]
    intent = OrderIntent(SYMBOL, "BUY", 100, bars[0].dt, market="CN", time_in_force="GTC", max_attempts=3)
    result = run_from_store(BarStore(bars), [intent], ExecutionConfig(
        initial_cash=1000, fee_mode="PER_ORDER", fee_calculator=make_calculator(),
        fill_model=VolumeParticipationFillModel(1.0, 10), policy_ref="registry:market-cn"),
        status_provider=lambda *_args: "TRADING")
    assert not result.fills and len(result.audits) == 1
    audit = result.audits[0]
    metadata = audit.query_metadata
    assert len(metadata["retry_trace"]) == 2
    assert metadata["terminal_reason"] == "ORDER_EXPIRED"
    assert audit.fee_mode == metadata["fee_mode"] == "PER_ORDER"
    assert audit.fee_source == metadata["fee_source"] == "none"
    assert audit.fee_breakdown == {}
    assert audit.fee_schedule_identity is None
    assert not any(key in metadata for key in ("fee_schedule_ref", "fee_schedule_version",
                                               "fee_schedule_identity", "fee_schedule_hash",
                                               "fee_breakdown", "aggregate_fee_breakdown"))
    assert result.cash == 1000 and result.positions == {}


def test_zero_candidate_order_has_per_order_audit_without_fee_charge():
    bar = make_bar(30, volume=100)
    intent = OrderIntent(SYMBOL, "BUY", 10, bar.dt, market="CN", order_id="no-candidate",
                         time_in_force="GTC", max_attempts=2)
    result = run_from_store(BarStore([bar]), [intent], ExecutionConfig(
        initial_cash=1000, fee_mode="PER_ORDER", fee_calculator=make_calculator(),
        policy_ref="registry:market-cn"), status_provider=lambda *_args: "TRADING")
    assert result.fills == () and result.cash == 1000 and result.inventory_lots == ()
    audit = result.audits[0]
    assert audit.status == "unfilled" and audit.reason == "ORDER_EXPIRED"
    assert audit.fee_mode == audit.query_metadata["fee_mode"] == "PER_ORDER"
    assert audit.fee_source == audit.query_metadata["fee_source"] == "none"
    assert audit.fee_breakdown == {} and "aggregate_fee_breakdown" not in audit.query_metadata
    assert audit.query_metadata["retry_trace"] == []


def test_insufficient_cash_on_first_attempt_rejects_without_side_effects():
    bars = [make_bar(30), make_bar(31, volume=10), make_bar(32, volume=10)]
    intent = OrderIntent(SYMBOL, "BUY", 20, bars[0].dt, market="CN", order_id="cash-first",
                         time_in_force="GTC", max_attempts=2)
    result = run_from_store(BarStore(bars), [intent], ExecutionConfig(
        initial_cash=105, lot_size=10, fee_mode="PER_ORDER", fee_calculator=make_calculator(commission_min="10"),
        fill_model=VolumeParticipationFillModel(1.0, 10), policy_ref="registry:market-cn"),
        status_provider=lambda *_args: "TRADING")
    assert result.fills == ()
    assert result.cash == 105 and result.positions == {} and result.inventory_lots == ()
    assert len(result.audits) == 1
    audit = result.audits[0]
    assert (audit.status, audit.reason, audit.fill_reason) == (
        "unfilled", "INSUFFICIENT_CASH", "INSUFFICIENT_CASH")
    assert audit.filled_quantity == 0 and audit.fill_count == 0 and audit.fill_at is None
    assert audit.fee_mode == "PER_ORDER" and audit.fee_source == "none"
    assert audit.query_metadata["fee_mode"] == "PER_ORDER"
    assert audit.query_metadata["fee_source"] == "none"
    assert audit.query_metadata["retry_reasons"] == ["INSUFFICIENT_CASH"]
    assert "aggregate_fee_breakdown" not in audit.query_metadata


def test_later_cash_rejection_preserves_prior_fill_and_is_terminal():
    bars = [make_bar(30), make_bar(31, volume=10), make_bar(32, volume=10),
            make_bar(33, volume=10)]
    intent = OrderIntent(SYMBOL, "BUY", 30, bars[0].dt, market="CN", order_id="cash-later",
                         time_in_force="GTC", max_attempts=3)
    result = run_from_store(BarStore(bars), [intent], ExecutionConfig(
        initial_cash=200.15, lot_size=10, fee_mode="PER_ORDER", fee_calculator=make_calculator(),
        fill_model=VolumeParticipationFillModel(1.0, 10), policy_ref="registry:market-cn"),
        status_provider=lambda *_args: "TRADING")
    assert len(result.fills) == 1
    fill = result.fills[0]
    assert fill.quantity == 10 and fill.fee == pytest.approx(0.1)
    assert fill.fee_mode == "PER_ORDER" and fill.fee_source == "fee_schedule"
    assert result.cash == pytest.approx(100.05)
    assert result.positions[SYMBOL] == 10
    assert sum(lot.quantity for lot in result.inventory_lots) == 10
    assert len(result.audits) == 1
    audit = result.audits[0]
    assert (audit.status, audit.reason, audit.fill_reason) == (
        "partial", "INSUFFICIENT_CASH", "INSUFFICIENT_CASH")
    assert audit.filled_quantity == 10 and audit.remaining_quantity == 20
    assert audit.fill_at == result.fills[-1].dt
    assert audit.fill_count == 1 and audit.fee_mode == "PER_ORDER"
    assert audit.fee_source == "fee_schedule"
    assert audit.query_metadata["retry_reasons"] == ["PARTIAL_FILL", "INSUFFICIENT_CASH"]
    assert audit.query_metadata["fee_mode"] == "PER_ORDER"
    assert audit.query_metadata["fee_source"] == "fee_schedule"
    assert audit.query_metadata["fee_schedule_identity"] == fill.fee_schedule_identity
    assert Decimal(audit.query_metadata["aggregate_fee_breakdown"]["total"]) == Decimal("0.10")
