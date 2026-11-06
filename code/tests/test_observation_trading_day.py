# -*- coding: utf-8 -*-
"""观察生成只在交易日：市场推断 + 日历过滤单测（不连库）。"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
SCRIPT = CODE / "scripts" / "ingest_observations_backtest.py"


def _load_ingest():
    spec = importlib.util.spec_from_file_location("ingest_observations_backtest_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


ingest = _load_ingest()


class _Row:
    def __init__(self, value):
        self.value = value

    def first(self):
        return (self.value,)


class _CalendarConn:
    """只回答 v2_market_calendar 查询的假连接。"""

    def __init__(self, open_markets: set[str]):
        self.open_markets = open_markets
        self.calls: list[dict] = []

    def execute(self, sql, params=None):
        assert "v2_market_calendar" in str(sql)
        self.calls.append(dict(params or {}))
        return _Row((params or {}).get("m") in self.open_markets)


def test_market_for_strategy():
    assert ingest._market_for_strategy("v1-cn-strat-2", "000001.SZ") == "CN"
    assert ingest._market_for_strategy("chanlun", "600000.SH") == "CN"
    assert ingest._market_for_strategy("v1-hk-strat-7", "00005") == "HK"
    assert ingest._market_for_strategy("v1-us-strat-6", "AAPL") == "US"
    assert ingest._market_for_strategy("v1-crypto-strat-11", "SOL/USDT") == "CRYPTO"
    assert ingest._market_for_strategy("qpzm", "sh688004") == "CN"
    assert ingest._market_for_strategy("qpzm", "00700") == "HK"
    assert ingest._market_for_strategy("qpzm", "BTC/USDT") == "CRYPTO"
    assert ingest._market_for_strategy("unknown", "") is None


def test_is_market_open_uses_calendar():
    day = date(2026, 10, 31)
    assert ingest._is_market_open(_CalendarConn({"CRYPTO"}), "CRYPTO", day) is True
    assert ingest._is_market_open(_CalendarConn({"CRYPTO"}), "CN", day) is False
    assert ingest._is_market_open(_CalendarConn({"CRYPTO"}), "US", day) is False


def test_filter_skips_closed_markets_keeps_crypto_weekend():
    day = date(2026, 10, 31)
    rows = [
        dict(strategy_id="v1-cn-strat-2", signal_date=day, code="000001.SZ",
             name="A", signal="buy", market="CN"),
        dict(strategy_id="v1-hk-strat-7", signal_date=day, code="00005",
             name="B", signal="buy", market="HK"),
        dict(strategy_id="v1-crypto-strat-11", signal_date=day, code="SOL/USDT",
             name="C", signal="buy", market="CRYPTO"),
    ]
    pending, skipped = ingest._filter_trading_day_pending(
        _CalendarConn({"CRYPTO"}), rows)
    assert [(r["strategy_id"], r["signal_date"], r["code"], r["status"])
            for r in pending] == [
        ("v1-crypto-strat-11", day, "SOL/USDT", "pending"),
    ]
    assert skipped == {"CN": 1, "HK": 1}


def test_filter_infers_market_when_missing():
    day = date(2026, 10, 31)
    rows = [
        dict(strategy_id="v1-us-strat-6", signal_date=day, code="AAPL",
             name="A", signal="buy", market=None),
        dict(strategy_id="v1-crypto-strat-9", signal_date=day, code="PROM/USDT",
             name="B", signal="buy", market=None),
    ]
    pending, skipped = ingest._filter_trading_day_pending(
        _CalendarConn({"CRYPTO"}), rows)
    assert [r["strategy_id"] for r in pending] == ["v1-crypto-strat-9"]
    assert skipped == {"US": 1}
