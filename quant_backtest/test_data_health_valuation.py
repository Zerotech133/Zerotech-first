import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

import data_health


def test_health_annotates_valuation_fields_from_daily_basic_source(tmp_path, monkeypatch):
    data = tmp_path / "data"
    ashare = data / "ashare"
    basic = data / "tdx" / "ts_daily_basic"
    ashare.mkdir(parents=True)
    basic.mkdir(parents=True)
    pq.write_table(pa.Table.from_pandas(pd.DataFrame([
        {"ts_code": "000002.SZ", "trade_date": "20261009", "close": 10.0},
    ]), preserve_index=False), ashare / "000002_SZ.parquet")
    pq.write_table(pa.Table.from_pandas(pd.DataFrame([
        {"ts_code": "000002.SZ", "trade_date": "20261009", "total_mv": 12.0,
         "pe": 8.0, "turnover_rate": 1.5},
    ]), preserve_index=False), basic / "part_0000.parquet")
    monkeypatch.setattr(data_health, "DATA", str(data))
    kline = {"cn": {}}
    data_health._annotate_cn_sample(kline)
    assert kline["cn"]["missing_fields"] == []
    assert kline["cn"]["valuation_fields"]["turnover"]["coverage"] == 1.0
