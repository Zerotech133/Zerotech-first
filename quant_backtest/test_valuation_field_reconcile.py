import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from valuation_field_reconcile import (
    classify_valuation_null,
    join_daily_basic_from_dir,
    join_daily_basic_to_kline,
    valuation_field_coverage,
)


def test_join_maps_daily_basic_units_without_mutating_source():
    kline = pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20261001", "close": 10.0}])
    basic = pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20261001", "total_mv": 123.4, "pe": 8.5, "turnover_rate": 2.1}])
    out = join_daily_basic_to_kline(kline, basic)
    assert out.loc[0, "market_cap"] == 123.4 * 10000
    assert out.loc[0, "pe"] == 8.5
    assert out.loc[0, "turnover"] == 2.1
    assert "market_cap" not in basic.columns


def test_join_fills_existing_empty_valuation_columns_from_basic():
    kline = pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20261001",
                           "pe": pd.NA, "turnover": pd.NA}])
    basic = pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20261001",
                           "total_mv": 2.0, "pe": 9.0, "turnover_rate": 3.0}])
    out = join_daily_basic_to_kline(kline, basic)
    assert out.loc[0, "pe"] == 9.0
    assert out.loc[0, "turnover"] == 3.0


def test_classify_valuation_null_preserves_legal_pe_null():
    assert classify_valuation_null({"pe": None, "net_profit": -1, "market_cap": 10, "turnover": 2}) == "legal_null_loss"
    assert classify_valuation_null({"pe": None, "suspended": True, "market_cap": 10, "turnover": None}) == "legal_null_suspended"
    assert classify_valuation_null({"pe": None, "net_profit": 1, "market_cap": None, "turnover": None}) == "unexplained_null"


def test_join_from_daily_basic_parts_only_reads_matching_keys(tmp_path):
    source = tmp_path / "ts_daily_basic"
    source.mkdir()
    table = pa.Table.from_pandas(pd.DataFrame([
        {"ts_code": "000001.SZ", "trade_date": "20261001", "total_mv": 10.0,
         "pe": 5.0, "turnover_rate": 1.2},
        {"ts_code": "000002.SZ", "trade_date": "20261001", "total_mv": 20.0,
         "pe": 6.0, "turnover_rate": 1.3},
    ]), preserve_index=False)
    pq.write_table(table, source / "part_0000.parquet")
    view = join_daily_basic_from_dir(
        pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20261001", "close": 1.0}]),
        source,
    )
    assert view.loc[0, "market_cap"] == 100000.0
    assert view.loc[0, "turnover"] == 1.2
    assert valuation_field_coverage(view)["fields"]["pe"]["coverage"] == 1.0
    assert valuation_field_coverage(view)["source_matched_rows"] == 1
    assert valuation_field_coverage(view)["source_unmatched_rows"] == 0
