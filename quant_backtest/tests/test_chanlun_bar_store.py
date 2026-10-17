# -*- coding: utf-8 -*-
"""Compatibility facade tests for explicit BarStore loading."""
from datetime import date, datetime, timezone
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bar_dto import Bar
from bar_store import BarStore
import chanlun_data


def _make_bar(dt: datetime, timeframe: str = "5m", symbol: str = "000001.SZ", market: str = "CN", closed: bool = True, available_at: datetime = None, close_at: datetime = None) -> Bar:
    return Bar(
        symbol=symbol,
        market=market,
        timeframe=timeframe,
        dt=dt,
        session_date=date(2026, 10, 16),
        o=10.0,
        h=11.0,
        l=9.0,
        c=10.5,
        v=0,
        amount=None,
        close_at=close_at if close_at is not None else dt,
        available_at=available_at if available_at is not None else dt,
        closed=closed,
        source="fixture",
        source_version="v1",
        adjustment="none",
        quality="observed",
    )


def test_explicit_store_preserves_legacy_shape_and_local_time():
    dt = datetime(2026, 10, 16, 1, 30, tzinfo=timezone.utc)
    store = BarStore([_make_bar(dt)])
    result = chanlun_data.load_kline(
        "000001.SZ", "5m", 20, bar_store=store, explain=True,
    )
    assert result["ok"] is True
    assert result["code"] == "000001.SZ"
    assert result["period"] == "5m"
    assert result["bars"] == [{
        "dt": "2026-10-16 09:30",
        "o": 10.0,
        "h": 11.0,
        "l": 9.0,
        "c": 10.5,
        "v": 0.0,
    }]
    assert result["metadata"]["sources"] == ["fixture"]


def test_store_filters_normalized_symbol_and_timeframe_and_limit():
    base = datetime(2026, 10, 16, 1, 30, tzinfo=timezone.utc)
    store = BarStore([
        _make_bar(base, "5m"),
        _make_bar(base.replace(minute=35), "5m"),
        _make_bar(base.replace(minute=40), "1m"),
        _make_bar(base.replace(minute=45), "5m", "600519.SH"),
    ])
    result = chanlun_data.load_kline("sz000001", "5m", 1, bar_store=store)
    assert result["ok"] is True
    assert len(result["bars"]) == 1
    assert result["bars"][0]["dt"] == "2026-10-16 09:35"
    assert result["code"] == "000001.SZ"


def test_store_query_validation_returns_legacy_error():
    result = chanlun_data.load_kline(
        "000001.SZ", "5m", 20,
        bar_store=BarStore(),
        as_of=datetime(2999, 1, 1, tzinfo=timezone.utc),
    )
    assert result["ok"] is False
    assert "加载失败" in result["error"]


def test_store_rejects_invalid_period_and_safe_limits():
    store = BarStore([_make_bar(datetime(2026, 10, 16, 1, 30, tzinfo=timezone.utc))])
    invalid = chanlun_data.load_kline("000001.SZ", "10m", None, bar_store=store)
    assert invalid["ok"] is False
    assert "5m" in invalid["error"]
    for limit in (None, "bad", -10, 0):
        result = chanlun_data.load_kline("000001.SZ", "5m", limit, bar_store=store)
        assert result["ok"] is True
        assert len(result["bars"]) == 1


def test_store_filters_market_and_combined_visibility_constraints():
    dt = datetime(2026, 10, 16, 1, 30, tzinfo=timezone.utc)
    future = dt.replace(minute=40)
    store = BarStore([
        _make_bar(dt, market="US"),
        _make_bar(dt, market="CN"),
        _make_bar(future, closed=False, available_at=future, close_at=future),
    ])
    result = chanlun_data.load_kline(
        "000001.SZ", "5m", 20, bar_store=store,
        as_of=dt, start=dt, end=future, closed_only=True,
    )
    assert result["ok"] is True
    assert len(result["bars"]) == 1


def test_invalid_period_mentions_5m():
    original = chanlun_data._db_query
    chanlun_data._db_query = lambda sql, params: []
    try:
        result = chanlun_data.load_kline("000001.SZ", "10m", 20)
    finally:
        chanlun_data._db_query = original
    assert result["ok"] is False
    assert "5m" in result["error"]
