# -*- coding: utf-8 -*-
"""Focused regression coverage for the opt-in overnight gap stop policy."""
from datetime import date, datetime, timezone
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bar_dto import Bar
from bar_store import BarStore
from execution_engine import ExecutionConfig, OrderIntent, run_from_store

UTC = timezone.utc


def make_bar(day: int, minute: int, open_price: float, close_price: float | None = None,
             *, available_at: datetime | None = None, close_at: datetime | None = None,
             closed: bool = True) -> Bar:
    """Build an observed CN 1m bar with explicit visibility evidence."""
    dt = datetime(2026, 10, day, 1, minute, tzinfo=UTC)
    close = open_price if close_price is None else close_price
    return Bar(
        "000001.SZ", "CN", "1m", dt, date(2026, 10, day), open_price,
        open_price, open_price, close, 100, 100,
        available_at or dt, close_at or dt, closed, "fixture", "v1", "none",
        "observed", ("fixture",),
    )


def run_stop(bars: list[Bar], *, threshold: float | None, stop_price: float = 90.0,
             slippage_bps: float = 0.0):
    """Run a stop sell from the first bar's signal into the second bar."""
    signal = bars[-2]
    return run_from_store(
        BarStore(bars),
        [OrderIntent("000001.SZ", "SELL", 100, signal.dt,
                      order_type="STOP", stop_price=stop_price)],
        ExecutionConfig(
            initial_cash=0.0,
            initial_positions={"000001.SZ": 100},
            t_plus_one=False,
            gap_stop_pct=threshold,
            slippage_bps=slippage_bps,
            policy_ref="registry:market-cn",
        ),
        status_provider=lambda *args: "TRADING",
    )


def test_gap_threshold_triggers_at_or_above_threshold_and_uses_open():
    prior = make_bar(15, 59, 100, 100)
    signal = make_bar(16, 30, 100)
    eligible = make_bar(16, 31, 80, 82)
    result = run_stop([prior, signal, eligible], threshold=0.20)

    assert result.fills[0].price == 80.0
    assert result.audits[0].reason == "GAP_STOP_TRIGGERED"
    assert result.audits[0].query_metadata["gap_pct"] == pytest.approx(0.20)
    assert result.audits[0].query_metadata["raw_price"] == 80.0


def test_below_threshold_open_cross_uses_open_price():
    prior = make_bar(15, 59, 100, 100)
    signal = make_bar(16, 30, 100)
    eligible = make_bar(16, 31, 95, 96)
    result = run_stop([prior, signal, eligible], threshold=0.10, stop_price=98)

    assert result.fills[0].price == 95.0
    assert result.audits[0].reason == "STOP_TRIGGERED"
    assert result.audits[0].query_metadata["gap_pct"] == pytest.approx(0.05)
    assert result.audits[0].query_metadata["raw_price"] == 95.0


def test_previous_close_is_last_bar_of_previous_session_not_same_session():
    previous_last = make_bar(15, 59, 100, 100)
    same_session_earlier = make_bar(16, 29, 200, 200)
    signal = make_bar(16, 30, 100)
    eligible = make_bar(16, 31, 80, 181)
    # The low must cross the stop to exercise the triggered gap path.
    eligible = Bar(
        eligible.symbol, eligible.market, eligible.timeframe, eligible.dt,
        eligible.session_date, eligible.o, eligible.h, 80.0, eligible.c,
        eligible.v, eligible.amount, eligible.close_at, eligible.available_at,
        eligible.closed, eligible.source, eligible.source_version, eligible.adjustment,
        eligible.quality, eligible.source_trace,
    )
    result = run_stop([previous_last, same_session_earlier, signal, eligible], threshold=0.10)

    metadata = result.audits[0].query_metadata
    assert result.audits[0].reason == "GAP_STOP_TRIGGERED"
    assert metadata["previous_close"] == 100.0
    assert metadata["previous_close_session"] == "2026-10-15"
    assert metadata["previous_close_dt"].endswith("01:59:00+00:00")


def test_missing_previous_close_fails_closed():
    signal = make_bar(16, 30, 100)
    eligible = make_bar(16, 31, 80, 82)
    result = run_stop([signal, eligible], threshold=0.10)

    assert not result.fills
    assert result.unfilled[0].reason == "GAP_PREVIOUS_CLOSE_MISSING"
    assert result.audits[0].query_metadata["trigger"] == "GAP_PREVIOUS_CLOSE_MISSING"


def test_invisible_previous_close_fails_closed():
    hidden_at = datetime(2026, 10, 16, 2, 0, tzinfo=UTC)
    previous = make_bar(15, 59, 100, 100, close_at=hidden_at)
    signal = make_bar(16, 30, 100)
    eligible = make_bar(16, 31, 80, 82)
    result = run_stop([previous, signal, eligible], threshold=0.10)

    assert not result.fills
    assert result.unfilled[0].reason == "GAP_PREVIOUS_CLOSE_MISSING"


def test_open_equal_stop_uses_normal_stop_without_gap_lookup():
    signal = make_bar(16, 30, 100)
    eligible = make_bar(16, 31, 90, 91)
    result = run_stop([signal, eligible], threshold=0.10, stop_price=90)

    assert result.fills[0].price == 90.0
    assert result.audits[0].reason == "STOP_TRIGGERED"
    assert "gap_pct" not in result.audits[0].query_metadata


def test_gap_stop_pct_must_be_less_than_one():
    with pytest.raises(ValueError, match=r"\[0, 1\)"):
        ExecutionConfig(gap_stop_pct=1.0)
    with pytest.raises(ValueError, match=r"\[0, 1\)"):
        ExecutionConfig(gap_stop_pct=1.1)


def test_market_order_ignores_gap_stop_policy():
    signal = make_bar(16, 30, 100)
    eligible = make_bar(16, 31, 80, 82)
    result = run_from_store(
        BarStore([signal, eligible]),
        [OrderIntent("000001.SZ", "BUY", 100, signal.dt)],
        ExecutionConfig(initial_cash=10000, gap_stop_pct=0.10,
                        policy_ref="registry:market-cn"),
        status_provider=lambda *args: "TRADING",
    )

    assert result.fills
    assert result.audits[0].reason == "FILLED"
    assert "gap_pct" not in result.audits[0].query_metadata


def test_gap_stop_applies_slippage_once_after_open_trigger():
    prior = make_bar(15, 59, 100, 100)
    signal = make_bar(16, 30, 100)
    eligible = make_bar(16, 31, 80, 82)
    result = run_stop([prior, signal, eligible], threshold=0.10, slippage_bps=100)

    assert result.fills[0].price == pytest.approx(79.2)
    assert result.fills[0].slippage == pytest.approx(80.0)
    assert result.audits[0].query_metadata["raw_price"] == 80.0
    assert result.audits[0].query_metadata["trigger"] == "GAP_STOP_TRIGGERED"
