# -*- coding: utf-8 -*-
from data_health import build_full_refresh_health


def test_health_flags_historical_gap_even_when_latest_date_is_complete():
    health = build_full_refresh_health(
        daily_stock={"20261008": {"actual": 2, "expected": 3}, "20261009": {"actual": 3, "expected": 3}},
        daily_sector={"20261008": {"actual": 1, "expected": 2}, "20261009": {"actual": 2, "expected": 2}},
        critical_nulls={"ts_margin_detail.rzrqye": 2},
        cross_table_keys={"daily_vs_basic": {"missing": 1, "duplicate": 0}},
        query_results={"income": {"rows": 0, "error": None}},
    )
    assert health["daily_unique_stock_coverage"]["status"] == "FAIL"
    assert health["daily_unique_sector_coverage"]["status"] == "FAIL"
    assert health["critical_nulls"]["value"] == 2
    assert health["cross_table_key_reconciliation"]["status"] == "FAIL"
    assert health["query_result_checks"]["status"] == "FAIL"

