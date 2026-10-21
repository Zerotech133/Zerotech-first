# -*- coding: utf-8 -*-
"""Isolated STOP SELL gap-price and fill-accounting adversarial tests."""
from datetime import date, datetime, timezone
from dataclasses import replace
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bar_dto import Bar
from bar_store import BarStore
from execution_engine import ExecutionConfig, InventoryLot, OrderIntent, run_from_store
from fill_model import VolumeParticipationFillModel


SYMBOL = "000001.SZ"


def bar(day: int, minute: int, opening: float, closing: float | None = None,
        *, low: float | None = None, volume: float = 100) -> Bar:
    """Construct a closed, immediately visible synthetic minute bar."""
    at = datetime(2026, 10, day, 1, minute, tzinfo=timezone.utc)
    return Bar(SYMBOL, "CN", "1m", at, date(2026, 10, day), opening,
               opening, opening if low is None else low,
               opening if closing is None else closing, volume,
               opening * volume, at, at, True, "synthetic", "1", "none", "observed")


def execute(prior: Bar | None, eligible: Bar, *, stop_price: float = 98,
            threshold: float = 0.1, model: VolumeParticipationFillModel | None = None,
            slippage_bps: float = 0, fee_bps: float = 0):
    """Submit one sell against an owned and settled CN lot."""
    signal = bar(16, 30, 100)
    opening_lot = InventoryLot(SYMBOL, "CN", "SZSE", "equity", date(2026, 10, 15),
                               date(2026, 10, 16), 100, "owned")
    bars = [signal, eligible] if prior is None else [prior, signal, eligible]
    intent = OrderIntent(SYMBOL, "SELL", 100, signal.dt, market="CN",
                         order_type="STOP", stop_price=stop_price, order_id="stop")
    config = ExecutionConfig(initial_cash=100, initial_lots=(opening_lot,),
                             market="CN", t_plus_one=True, gap_stop_pct=threshold,
                             fee_bps=fee_bps, slippage_bps=slippage_bps,
                             fill_model=model, policy_ref="registry:market-cn")
    return run_from_store(BarStore(bars), [intent], config,
                          status_provider=lambda *_args: "TRADING")


def test_open_below_stop_but_below_gap_threshold_executes_at_open() -> None:
    """A small overnight gap never fills a sell above the actual opening print."""
    result = execute(bar(15, 59, 100), bar(16, 31, 95),
                     fee_bps=10, slippage_bps=100)
    fill, audit = result.fills[0], result.audits[0]
    assert audit.reason == "STOP_TRIGGERED"
    assert audit.status == "filled"
    assert audit.query_metadata["gap_pct"] == pytest.approx(.05)
    assert audit.query_metadata["raw_price"] == 95
    assert fill.price == pytest.approx(94.05)
    assert fill.gross_value == pytest.approx(9405)
    assert fill.fee == pytest.approx(9.405)
    assert result.cash == pytest.approx(100 + fill.gross_value - fill.fee)
    assert result.metrics["fully_filled_count"] == 1
    assert result.metrics["partial_count"] == 0
    assert result.metrics["partial_filled_qty"] == 0


def test_gap_at_threshold_preserves_gap_trigger_and_open_price() -> None:
    """A sufficiently deep opening gap retains the distinct audit reason."""
    result = execute(bar(15, 59, 100), bar(16, 31, 80), stop_price=98,
                     threshold=.2)
    assert result.fills[0].price == 80
    assert result.audits[0].reason == "GAP_STOP_TRIGGERED"
    assert result.audits[0].status == "filled"
    assert result.audits[0].query_metadata["raw_price"] == 80
    assert result.metrics["fully_filled_count"] == 1


def test_intrabar_touch_uses_stop_not_open() -> None:
    """Only an intrabar crossing may use the stop trigger price."""
    result = execute(None, bar(16, 31, 100, low=97), stop_price=98)
    assert result.fills[0].price == 98
    assert result.audits[0].reason == "STOP_TRIGGERED"
    assert result.audits[0].status == "filled"


def test_missing_or_invisible_prior_close_rejects_gap_policy() -> None:
    """Prior close must have been observed before the candidate execution."""
    previous = bar(15, 59, 100)
    eligible = bar(16, 31, 80)
    future = replace(previous, available_at=datetime(2026, 10, 16, 2, tzinfo=timezone.utc))
    for prior in (None, future):
        result = execute(prior, eligible)
        assert result.fills == ()
        assert result.audits[0].reason == "GAP_PREVIOUS_CLOSE_MISSING"
        assert result.cash == 100
        assert result.metrics["fully_filled_count"] == 0


def test_partially_filled_stop_keeps_trigger_and_partial_metrics() -> None:
    """Half a STOP SELL remains partial and settles only accepted quantity."""
    result = execute(bar(15, 59, 100), bar(16, 31, 95, volume=50),
                     model=VolumeParticipationFillModel(1.0))
    fill, audit = result.fills[0], result.audits[0]
    assert fill.quantity == 50
    assert fill.price == 95
    assert audit.status == "partial"
    assert audit.reason == "STOP_TRIGGERED"
    assert audit.remaining_quantity == 50
    assert result.metrics["partial_count"] == 1
    assert result.metrics["fully_filled_count"] == 0
    assert result.metrics["partial_filled_qty"] == 50
    assert result.cash == pytest.approx(100 + fill.gross_value - fill.fee)
    assert result.instrument_positions[("CN", "SZSE", "equity", SYMBOL)] == 50
