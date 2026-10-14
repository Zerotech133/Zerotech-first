import pandas as pd

from margin_gap_reconciliation import (
    classify_margin_gap,
    reconcile_latest_stock_coverage,
    reconcile_margin_dates,
)


def test_classify_margin_gap_distinguishes_eligibility_states():
    basic = pd.DataFrame([
        {"ts_code": "000001.SZ", "list_date": "20200101", "list_status": "L", "is_margin": 1},
        {"ts_code": "000002.SZ", "list_date": "20200101", "list_status": "L", "is_margin": 0},
        {"ts_code": "000003.SZ", "list_date": "20270101", "list_status": "L", "is_margin": 1},
    ])
    assert classify_margin_gap({"ts_code": "000001.SZ", "trade_date": "20261001"}, basic, [], []) == "eligible_missing"
    assert classify_margin_gap({"ts_code": "000002.SZ", "trade_date": "20261001"}, basic, [], []) == "non_margin"
    assert classify_margin_gap({"ts_code": "000003.SZ", "trade_date": "20261001"}, basic, [], []) == "prelisting"
    assert classify_margin_gap({"ts_code": "000001.SZ", "trade_date": "20261001"}, basic, [{"ts_code": "000001.SZ", "trade_date": "20261001"}], []) == "suspended"
    assert classify_margin_gap({"ts_code": "000001.SZ", "trade_date": "20261001"}, basic, [], [{"ts_code": "000001.SZ", "effective_date": "20260930"}]) == "delisted"


def test_reconcile_margin_dates_reports_only_unexplained_missing():
    result = reconcile_margin_dates(
        ["20261001", "20261002"],
        [{"trade_date": "20261001", "status": "candidate_ready", "rows": [{"ts_code": "000001.SZ"}]}],
        {"20261001": {"000001.SZ", "000002.SZ"}, "20261002": {"000001.SZ"}},
    )
    assert result["missing"] == [{"trade_date": "20261001", "ts_code": "000002.SZ"}, {"trade_date": "20261002", "ts_code": "000001.SZ"}]
    assert result["missing_count"] == 2


def test_reconcile_latest_stock_coverage_excludes_suspended():
    basic = [{"ts_code": "000001.SZ", "list_date": "20200101", "list_status": "L"},
             {"ts_code": "000002.SZ", "list_date": "20200101", "list_status": "L"}]
    result = reconcile_latest_stock_coverage("20261001", [{"ts_code": "000001.SZ"}], basic,
                                            [{"ts_code": "000002.SZ", "trade_date": "20261001"}])
    assert result["missing_expected"] == []
    assert result["eligible_suspended"] == ["000002.SZ"]

