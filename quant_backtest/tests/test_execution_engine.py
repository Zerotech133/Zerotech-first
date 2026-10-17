# -*- coding: utf-8 -*-
from dataclasses import replace
from datetime import date, datetime, timezone
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bar_dto import Bar, BarQuery
from bar_store import BarStore
from execution_engine import ExecutionConfig, OrderIntent, run_from_store


def make_bar(symbol, minute, open_price, close_price=None, market="CN", timeframe="1m", closed=True, adjustment="none"):
    dt = datetime(2026, 10, 16, 1, minute, tzinfo=timezone.utc)
    close = close_price if close_price is not None else open_price
    return Bar(symbol, market, timeframe, dt, date(2026, 10, 16), open_price, open_price, open_price, close, 100, 100, dt, dt, closed, "fixture", "v1", adjustment, "observed", ("fixture",))


def test_next_bar_fee_slippage_lot_and_audit():
    store = BarStore([make_bar("000001.SZ", 30, 10), make_bar("000001.SZ", 31, 11, 12)])
    submitted = datetime(2026, 10, 16, 1, 30, tzinfo=timezone.utc)
    result = run_from_store(store, [OrderIntent("000001.SZ", "BUY", 105, submitted)], ExecutionConfig(initial_cash=2000, fee_bps=10, slippage_bps=100, lot_size=100, policy_ref="registry:market-cn"), status_provider=lambda *args: "TRADING")
    assert len(result.fills) == 1
    assert result.fills[0].quantity == 100
    assert result.fills[0].price == 11.11
    assert result.audits[0].status == "filled"
    assert result.schema == "minute-exec-v1"
    assert result.metrics["unfilled_rate"] == 0
    assert result.source_metadata["sources"] == ["fixture"]


def test_t_plus_one_uses_session_date_and_unfilled_rate():
    bars = [make_bar("000001.SZ", 30, 10), make_bar("000001.SZ", 31, 10)]
    store = BarStore(bars)
    t0 = bars[0].dt
    result = run_from_store(store, [OrderIntent("000001.SZ", "BUY", 100, t0), OrderIntent("000001.SZ", "SELL", 100, t0)], ExecutionConfig(initial_cash=2000, policy_ref="registry:market-cn"), status_provider=lambda *args: "TRADING")
    assert [item.status for item in result.audits] == ["filled", "unfilled"]
    assert result.audits[1].reason == "T_PLUS_ONE"
    assert result.unfilled_rate == 0.5


def test_status_limit_and_unknown_reasons():
    bars = [make_bar("000001.SZ", 30, 10), make_bar("000001.SZ", 31, 20)]
    store = BarStore(bars)
    intent = OrderIntent("000001.SZ", "BUY", 100, bars[0].dt)
    halted = run_from_store(store, [intent], ExecutionConfig(initial_cash=5000, policy_ref="registry:market-cn"), status_provider=lambda *args: "suspended")
    assert halted.unfilled[0].reason == "HALTED"
    unknown = run_from_store(store, [intent], ExecutionConfig(initial_cash=5000, policy_ref="registry:market-cn"), status_provider=lambda *args: None)
    assert unknown.unfilled[0].reason == "STATUS_UNKNOWN"
    limited = run_from_store(store, [intent], ExecutionConfig(initial_cash=5000, policy_ref="registry:market-cn"), limit_provider=lambda *args: {"limit_up": 20}, status_provider=lambda *args: "TRADING")
    assert limited.unfilled[0].reason == "LIMIT_UP"


def test_stop_order_gap_uses_ohlc_but_normal_buy_is_not_gap_blocked():
    bars = [make_bar("000001.SZ", 30, 10), make_bar("000001.SZ", 31, 20, close_price=19)]
    store = BarStore(bars)
    ordinary = run_from_store(store, [OrderIntent("000001.SZ", "BUY", 100, bars[0].dt)], ExecutionConfig(initial_cash=5000, gap_stop_pct=.2, policy_ref="registry:market-cn"), status_provider=lambda *args: "TRADING")
    assert ordinary.fills
    stop = run_from_store(store, [OrderIntent("000001.SZ", "SELL", 100, bars[0].dt, order_type="STOP", stop_price=21)], ExecutionConfig(initial_cash=5000, initial_positions={"000001.SZ": 100}, gap_stop_pct=.2, t_plus_one=False, policy_ref="registry:market-cn"), status_provider=lambda *args: "TRADING")
    assert stop.fills
    assert stop.fills[0].price == 20


def test_stop_order_is_subject_to_t_plus_one_and_limit_provider():
    bars = [make_bar("000001.SZ", 30, 10), make_bar("000001.SZ", 31, 8), make_bar("000001.SZ", 32, 8)]
    store = BarStore(bars)
    buy = OrderIntent("000001.SZ", "BUY", 100, bars[0].dt)
    intent = OrderIntent("000001.SZ", "SELL", 100, bars[1].dt, order_type="STOP", stop_price=9)
    blocked_t1 = run_from_store(store, [buy, intent], ExecutionConfig(initial_cash=1000, initial_positions={"000001.SZ": 0}, t_plus_one=True, policy_ref="registry:market-cn"), status_provider=lambda *args: "TRADING")
    assert blocked_t1.unfilled[-1].reason == "T_PLUS_ONE"
    blocked_limit = run_from_store(store, [intent], ExecutionConfig(initial_cash=1000, initial_positions={"000001.SZ": 100}, t_plus_one=False, policy_ref="registry:market-cn"), limit_provider=lambda *args: {"limit_down": 8}, status_provider=lambda *args: "TRADING")
    assert blocked_limit.unfilled[0].reason == "LIMIT_DOWN"


def test_query_metadata_is_json_safe_and_missing_signal_is_audit():
    bar = make_bar("000001.SZ", 30, 10)
    store = BarStore([bar])
    intent = OrderIntent("000001.SZ", "BUY", 100, bar.dt)
    result = run_from_store(store, [intent], ExecutionConfig(initial_cash=2000, policy_ref="registry:market-cn"), query=BarQuery(end=bar.dt))
    assert result.unfilled[0].reason == "NO_NEXT_BAR"
    assert isinstance(result.query_metadata["end"], str)


def test_strict_eligibility_rejects_delayed_submission_and_future_evidence():
    signal = make_bar("000001.SZ", 30, 10)
    candidate = make_bar("000001.SZ", 31, 11)
    store = BarStore([signal, candidate])
    delayed = OrderIntent(
        "000001.SZ", "BUY", 100, signal.dt.replace(second=30),
        signal_at=signal.dt,
    )
    same_bar = run_from_store(
        store,
        [delayed],
        ExecutionConfig(initial_cash=2000, next_bar=False, policy_ref="registry:market-cn"),
        status_provider=lambda *args: "TRADING",
    )
    assert same_bar.unfilled[0].reason == "NO_NEXT_BAR"
    assert same_bar.query_metadata["eligibility_policy_ref"] == "same_bar_inclusive_v1"
    assert same_bar.audits[0].eligibility_policy_ref == "same_bar_inclusive_v1"

    for field in ("available_at", "close_at"):
        future = replace(candidate, **{field: candidate.dt.replace(second=1)})
        result = run_from_store(
            BarStore([signal, future]),
            [OrderIntent("000001.SZ", "BUY", 100, signal.dt)],
            ExecutionConfig(initial_cash=2000, policy_ref="registry:market-cn"),
            status_provider=lambda *args: "TRADING",
        )
        assert result.unfilled[0].reason == "NO_NEXT_BAR"


def test_strict_eligibility_rejects_unclosed_execution_bar():
    signal = make_bar("000001.SZ", 30, 10)
    candidate = make_bar("000001.SZ", 31, 11, closed=False)
    result = run_from_store(
        BarStore([signal, candidate]),
        [OrderIntent("000001.SZ", "BUY", 100, signal.dt)],
        ExecutionConfig(initial_cash=2000, policy_ref="registry:market-cn"),
        status_provider=lambda *args: "TRADING",
    )
    assert result.unfilled[0].reason == "NO_NEXT_BAR"


def test_signal_visibility_checks_close_and_available_at_submission():
    signal = make_bar("000001.SZ", 30, 10)
    candidate = make_bar("000001.SZ", 31, 11)
    for field in ("available_at", "close_at"):
        hidden_signal = replace(signal, **{field: signal.dt.replace(second=1)})
        result = run_from_store(
            BarStore([hidden_signal, candidate]),
            [OrderIntent("000001.SZ", "BUY", 100, signal.dt)],
            ExecutionConfig(initial_cash=2000, policy_ref="registry:market-cn"),
            status_provider=lambda *args: "TRADING",
        )
        assert result.unfilled[0].reason == "SIGNAL_BAR_NOT_CLOSED"


def test_signal_requires_closed_observed_bar_and_adjustment_none():
    bar = make_bar("000001.SZ", 30, 10, closed=False)
    with_result = run_from_store(BarStore([bar, make_bar("000001.SZ", 31, 11)]), [OrderIntent("000001.SZ", "BUY", 100, bar.dt)], ExecutionConfig(initial_cash=2000, policy_ref="registry:market-cn"))
    assert with_result.unfilled[0].reason == "SIGNAL_BAR_NOT_CLOSED"
    adjusted = BarStore([make_bar("000001.SZ", 30, 10, adjustment="hfq")])
    try:
        run_from_store(adjusted, [OrderIntent("000001.SZ", "BUY", 100, bar.dt)], ExecutionConfig(policy_ref="registry:market-cn"), status_provider=lambda *args: "TRADING")
    except ValueError as exc:
        assert "adjustment must be none" in str(exc)
    else:
        raise AssertionError("adjusted bars must be rejected")
