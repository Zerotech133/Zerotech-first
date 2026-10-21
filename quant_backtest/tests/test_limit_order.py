# -*- coding: utf-8 -*-
from dataclasses import replace
from datetime import date, datetime, timezone
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bar_dto import Bar
from bar_store import BarStore
from execution_engine import ExecutionConfig, FillDecision, OrderIntent, VolumeParticipationFillModel, run_from_store


def make_bar(symbol, minute, open_price, close_price=None):
    dt = datetime(2026, 10, 16, 1, minute, tzinfo=timezone.utc)
    close = open_price if close_price is None else close_price
    return Bar(symbol, "CN", "1m", dt, date(2026, 10, 16), open_price, open_price, open_price,
               close, 100, 100, dt, dt, True, "fixture", "v1", "none", "observed", ("fixture",))


def limit_bars(open_price, high_price, low_price, close_price=None):
    signal = make_bar("000001.SZ", 30, 10)
    execution = replace(make_bar("000001.SZ", 31, open_price, close_price), h=high_price, l=low_price)
    return signal, execution


def run_limit(signal, execution, side, limit_price, *, quantity=100, config=None, fill_model=None, limit_provider=None):
    policy = config or ExecutionConfig(initial_cash=10000, t_plus_one=False,
                                       fill_model=fill_model, policy_ref="registry:market-cn")
    if config is not None and fill_model is not None:
        policy = replace(policy, fill_model=fill_model)
    intent = OrderIntent("000001.SZ", side, quantity, signal.dt, order_type="LIMIT",
                         limit_price=limit_price, market="CN")
    return run_from_store(BarStore([signal, execution]), [intent], policy,
                          status_provider=lambda *args: "TRADING", limit_provider=limit_provider)


def test_limit_buy_not_reached_has_no_side_effects_and_metadata():
    signal, execution = limit_bars(11, 12, 11)
    result = run_limit(signal, execution, "BUY", 10)
    audit = result.audits[0]
    assert audit.reason == "LIMIT_NOT_REACHED"
    assert not result.fills and result.cash == 10000 and result.positions == {}
    assert audit.query_metadata["order_type"] == "LIMIT"
    assert audit.query_metadata["limit_trigger"] is None


def test_limit_buy_low_touch_and_open_cross():
    signal, execution = limit_bars(11, 12, 10)
    result = run_limit(signal, execution, "BUY", 10.5)
    assert result.fills[0].price == 10.5
    assert result.audits[0].query_metadata["limit_trigger"] == "LOW_TOUCH"
    signal, execution = limit_bars(9, 12, 8)
    result = run_limit(signal, execution, "BUY", 10)
    assert result.fills[0].price == 9
    assert result.audits[0].query_metadata["limit_trigger"] == "OPEN_CROSS"


def test_limit_sell_not_reached_high_touch_and_open_cross():
    inventory = {"000001.SZ": 100}
    config = ExecutionConfig(initial_cash=10000, initial_positions=inventory, t_plus_one=False,
                             policy_ref="registry:market-cn")
    signal, execution = limit_bars(9, 10, 8)
    assert run_limit(signal, execution, "SELL", 11, config=config).unfilled[0].reason == "LIMIT_NOT_REACHED"
    signal, execution = limit_bars(9, 12, 8)
    result = run_limit(signal, execution, "SELL", 11, config=config)
    assert result.fills[0].price == 11
    assert result.audits[0].query_metadata["limit_trigger"] == "HIGH_TOUCH"
    signal, execution = limit_bars(12, 13, 11)
    result = run_limit(signal, execution, "SELL", 11, config=config)
    assert result.fills[0].price == 12
    assert result.audits[0].query_metadata["limit_trigger"] == "OPEN_CROSS"


def test_limit_ohlc_missing_and_close_only_crossing():
    signal, execution = limit_bars(11, 12, 11, close_price=9)
    assert run_limit(signal, execution, "BUY", 10).unfilled[0].reason == "LIMIT_NOT_REACHED"
    signal, execution = limit_bars(11, 12, 10)
    assert run_limit(signal, replace(execution, h=None), "BUY", 10).unfilled[0].reason == "LIMIT_DATA_MISSING"


def test_limit_slippage_clamp_and_rule_metadata_preservation():
    signal, execution = limit_bars(11, 12, 10)
    result = run_limit(signal, execution, "BUY", 10,
                       config=ExecutionConfig(initial_cash=10000, slippage_bps=200, t_plus_one=False,
                                              policy_ref="registry:market-cn", market_rules_required=True),
                       limit_provider=lambda *args: {"limit_up": 100})
    assert result.fills[0].price == 10
    metadata = result.audits[0].query_metadata
    assert metadata["slipped_price"] == 10.2 and metadata["final_price"] == 10
    assert metadata["slippage_clamped"] is True and metadata["rule_ref"]


def test_limit_model_breach_and_partial_fill():
    signal, execution = limit_bars(9, 12, 8)
    class BreachingModel:
        def evaluate(self, intent, bar, requested_quantity, reference_price):
            return FillDecision(requested_quantity, 11.0, "FILLED", "FILLED", "test", "1", ())
    result = run_limit(signal, execution, "BUY", 10, fill_model=BreachingModel())
    assert result.unfilled[0].reason == "LIMIT_MODEL_PRICE_BREACH"
    assert result.cash == 10000 and result.positions == {}
    signal, execution = limit_bars(11, 12, 10)
    result = run_limit(signal, execution, "BUY", 10, quantity=100,
                       config=ExecutionConfig(initial_cash=10000, slippage_bps=200, lot_size=10,
                                              t_plus_one=False, policy_ref="registry:market-cn"),
                       fill_model=VolumeParticipationFillModel(participation_rate=0.5, lot_size=10))
    assert result.audits[0].status == "partial" and result.fills[0].quantity == 50
    assert result.fills[0].price == 10 and result.positions["000001.SZ"] == 50
    # Historical `filled` counts emitted Fill records, including partial fills.
    assert result.metrics["filled"] == len(result.fills) == 1
    assert result.metrics["fully_filled_count"] == 0
    assert result.metrics["partial_count"] == 1
