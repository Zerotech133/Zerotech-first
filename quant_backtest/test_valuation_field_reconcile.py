import pandas as pd

from valuation_field_reconcile import classify_valuation_null, join_daily_basic_to_kline


def test_join_maps_daily_basic_units_without_mutating_source():
    kline = pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20261001", "close": 10.0}])
    basic = pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20261001", "total_mv": 123.4, "pe": 8.5, "turnover_rate": 2.1}])
    out = join_daily_basic_to_kline(kline, basic)
    assert out.loc[0, "market_cap"] == 123.4 * 10000
    assert out.loc[0, "pe"] == 8.5
    assert out.loc[0, "turnover"] == 2.1
    assert "market_cap" not in basic.columns


def test_classify_valuation_null_preserves_legal_pe_null():
    assert classify_valuation_null({"pe": None, "net_profit": -1, "market_cap": 10, "turnover": 2}) == "legal_null_loss" 
    assert classify_valuation_null({"pe": None, "suspended": True, "market_cap": 10, "turnover": None}) == "legal_null_suspended"
    assert classify_valuation_null({"pe": None, "net_profit": 1, "market_cap": None, "turnover": None}) == "unexplained_null"

