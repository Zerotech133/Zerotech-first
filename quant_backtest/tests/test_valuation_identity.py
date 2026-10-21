# -*- coding: utf-8 -*-
"""Isolation tests for explicit mark identity, currency and visibility."""
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bar_dto import Bar, BarQuery
from bar_store import BarStore
from execution_engine import ExecutionConfig, InventoryLot, OrderIntent, run_from_store


SYMBOL = "SAME.SZ"
AT = datetime(2026, 10, 16, 1, 30, tzinfo=timezone.utc)


def bar(minute: int, *, market: str = "US", exchange: str | None = "NYSE",
        currency: str | None = "USD", price: float = 20) -> Bar:
    """Create a visible synthetic quote without external market dependencies."""
    at = AT.replace(minute=minute)
    return Bar(SYMBOL, market, "1m", at, at.date(), price, price, price,
               price, 100, price * 100, at, at, True, "fixture", "1",
               "none", "observed", (), None, None, exchange, "equity", currency)


def lot(market: str = "US", exchange: str = "NYSE") -> InventoryLot:
    """Return settled inventory with a four-part, exact instrument identity."""
    return InventoryLot(SYMBOL, market, exchange, "equity", date(2026, 10, 15),
                        date(2026, 10, 16), 1, "held")


def config(*, currency: str = "USD", market: str | None = None,
           opening: tuple[InventoryLot, ...] = ()) -> ExecutionConfig:
    """Build an isolated no-network execution policy."""
    return ExecutionConfig(initial_cash=100, initial_lots=opening,
                           market=market, currency=currency,
                           t_plus_one=market != "US", policy_ref="registry:market-cn")


def test_us_nyse_full_identity_and_usd_equity() -> None:
    """Explicit USD NYSE quote values NYSE lots without erasing identity."""
    result = run_from_store(BarStore([bar(30)]), [], config(opening=(lot(),)))
    assert result.equity == pytest.approx(120)
    assert result.instrument_positions == {("US", "NYSE", "equity", SYMBOL): 1}


def test_cn_sell_cannot_borrow_us_lot_even_with_both_marks() -> None:
    """A fully valued foreign holding is never collateral for a CN sale."""
    cn_signal = bar(30, market="CN", exchange="SZSE", currency="CNY")
    cn_next = bar(31, market="CN", exchange="SZSE", currency="CNY")
    us_mark = bar(30)
    order = OrderIntent(SYMBOL, "SELL", 1, cn_signal.dt, market="CN", exchange="SZSE")
    result = run_from_store(BarStore([cn_signal, cn_next, us_mark]), [order],
                            config(currency="USD", opening=(lot(),)))
    assert result.fills == ()
    assert result.audits[0].reason == "ORDER_CURRENCY_MISMATCH"
    assert result.instrument_positions == {("US", "NYSE", "equity", SYMBOL): 1}


@pytest.mark.parametrize("mode", ["DAY", "GTC", "PER_ORDER"])
def test_missing_fx_fails_all_execution_modes(mode: str) -> None:
    """A correct USD price cannot be added to CNY cash without FX evidence."""
    quote = bar(30)
    order = OrderIntent(SYMBOL, "BUY", 1, quote.dt, market="US", exchange="NYSE",
                        time_in_force="GTC" if mode == "GTC" else "DAY")
    policy = config(currency="CNY", opening=(lot(),))
    if mode == "PER_ORDER":
        policy = replace(policy, fee_mode="PER_ORDER")
    with pytest.raises(ValueError, match="FX_RATE_UNAVAILABLE"):
        run_from_store(BarStore([quote, bar(31)]), [order], policy)


@pytest.mark.parametrize("case", ["no_quote", "wrong_exchange", "future", "bad_price", "no_currency"])
def test_no_compatible_mark_rejects_instead_of_zero(case: str) -> None:
    """Missing identity, visible price or currency may not silently mark zero."""
    fixture = bar(30)
    if case == "wrong_exchange":
        fixture = replace(fixture, exchange="NASDAQ")
    elif case == "future":
        fixture = replace(fixture, available_at=AT.replace(minute=32))
    elif case == "bad_price":
        fixture = replace(fixture, c=float("nan"), o=float("nan"))
    elif case == "no_currency":
        fixture = replace(fixture, quote_currency=None)
    data = [] if case == "no_quote" else [fixture]
    with pytest.raises(ValueError, match="VALUATION_PRICE_UNAVAILABLE"):
        run_from_store(BarStore(data), [], config(opening=(lot(),)),
                       query=BarQuery(as_of=AT))


@pytest.mark.parametrize("mode", ["DAY", "GTC"])
def test_cross_currency_order_cannot_mutate_cash(mode: str) -> None:
    """A USD order under CNY policy is rejected before matching or charging."""
    signal, eligible = bar(30), bar(31)
    order = OrderIntent(SYMBOL, "BUY", 1, signal.dt, market="US", exchange="NYSE",
                        time_in_force=mode)
    result = run_from_store(BarStore([signal, eligible]), [order], config(currency="CNY"))
    assert not result.fills
    assert result.cash == 100
    assert result.audits[0].reason == "ORDER_CURRENCY_MISMATCH"


def test_cn_legacy_suffix_does_not_override_conflicting_currency() -> None:
    """An explicit wrong CN quote currency is not silently rewritten to CNY."""
    signal = bar(30, market="CN", exchange=None, currency="USD")
    eligible = bar(31, market="CN", exchange=None, currency="USD")
    order = OrderIntent(SYMBOL, "BUY", 1, signal.dt, market="CN", exchange="SZSE")
    result = run_from_store(BarStore([signal, eligible]), [order],
                            config(currency="CNY"))
    assert result.fills == ()
    assert result.cash == 100
    assert result.audits[0].reason == "BAR_IDENTITY_UNAVAILABLE"


def test_conflicting_quotes_for_same_identity_reject_valuation() -> None:
    """USD and CNY quotes for one exact identity cannot be silently combined."""
    first = bar(30, currency="USD")
    second = bar(31, currency="CNY")
    with pytest.raises(ValueError, match="VALUATION_CURRENCY_CONFLICT"):
        run_from_store(BarStore([first, second]), [], config(opening=(lot(),)))


def test_unknown_cn_ticker_needs_explicit_identity_and_currency() -> None:
    """A bare ticker has no safe CN exchange or currency inference."""
    signal = replace(bar(30, market="CN", exchange=None, currency=None), symbol="ABC")
    eligible = replace(bar(31, market="CN", exchange=None, currency=None), symbol="ABC")
    order = OrderIntent("ABC", "BUY", 1, signal.dt, market="CN", exchange="SZSE")
    result = run_from_store(BarStore([signal, eligible]), [order], config(currency="CNY"))
    assert result.fills == ()
    assert result.cash == 100
    assert result.audits[0].reason == "BAR_IDENTITY_UNAVAILABLE"


@pytest.mark.parametrize("field", ["market", "exchange", "asset_class", "quote_currency"])
@pytest.mark.parametrize("placeholder", ["UNKNOWN", "unknown", " UnKnOwN "])
def test_placeholder_bar_never_values_owned_lot(field: str, placeholder: str) -> None:
    """Explicit placeholder text is not provenance for any identity dimension."""
    fixture = replace(bar(30), **{field: placeholder})
    with pytest.raises(ValueError, match="VALUATION_PRICE_UNAVAILABLE"):
        run_from_store(BarStore([fixture]), [], config(opening=(lot(),)))


@pytest.mark.parametrize("field", ["market", "exchange", "asset_class", "quote_currency"])
def test_placeholder_bar_and_order_cannot_both_trade(field: str) -> None:
    """Matching the same UNKNOWN text in order and quote grants no ownership."""
    signal = replace(bar(30), **{field: " UnKnOwN "})
    eligible = replace(bar(31), **{field: " UnKnOwN "})
    order_kwargs = {"market": "US", "exchange": "NYSE", "asset_class": "equity"}
    if field in order_kwargs:
        order_kwargs[field] = "UNKNOWN"
    order = OrderIntent(SYMBOL, "BUY", 1, signal.dt, **order_kwargs)
    result = run_from_store(BarStore([signal, eligible]), [order], config())
    assert result.fills == ()
    assert result.cash == 100
    assert result.inventory_lots == ()
    expected_reason = "SIGNAL_BAR_MISSING" if field == "market" else "BAR_IDENTITY_UNAVAILABLE"
    assert result.audits[0].reason == expected_reason


def test_unknown_opening_lot_cannot_be_valued() -> None:
    """A lot labeled UNKNOWN never inherits a same-placeholder price."""
    fixture = replace(bar(30, market="CN", exchange="UNKNOWN", currency="CNY"),
                      symbol="ABC")
    opening = InventoryLot("ABC", "CN", "UNKNOWN", "equity", None, None, 1, "unknown")
    with pytest.raises(ValueError, match="VALUATION_PRICE_UNAVAILABLE"):
        run_from_store(BarStore([fixture]), [], config(currency="CNY", opening=(opening,)))


def test_unknown_cn_symbol_and_order_cannot_create_unknown_lot() -> None:
    """Reproduce the independently verified synthetic placeholder cash exploit."""
    signal = replace(bar(30, market="CN", exchange="UNKNOWN", currency="CNY"),
                     symbol="ABC", o=10, h=10, l=10, c=10)
    eligible = replace(signal, dt=AT.replace(minute=31),
                       available_at=AT.replace(minute=31), close_at=AT.replace(minute=31))
    order = OrderIntent("ABC", "BUY", 1, signal.dt, market="CN", exchange="UNKNOWN")
    result = run_from_store(BarStore([signal, eligible]), [order], config(currency="CNY"))
    assert result.fills == ()
    assert result.cash == 100
    assert result.inventory_lots == ()
    assert result.audits[0].reason == "BAR_IDENTITY_UNAVAILABLE"


def test_us_unknown_exchange_cannot_satisfy_nyse_order() -> None:
    """Omitting an exchange is not evidence for a same-symbol NYSE order."""
    signal, eligible = bar(30, exchange=None), bar(31, exchange=None)
    order = OrderIntent(SYMBOL, "BUY", 1, signal.dt, market="US", exchange="NYSE")
    result = run_from_store(BarStore([signal, eligible]), [order], config())
    assert result.fills == ()
    assert result.audits[0].reason == "BAR_IDENTITY_UNAVAILABLE"
