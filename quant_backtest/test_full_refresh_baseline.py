import json

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from full_refresh_baseline import build_full_refresh_baseline


def _write_part(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pandas(pd.DataFrame(rows), preserve_index=False), path)


def test_baseline_uses_actual_queries_and_reports_duplicate_keys(tmp_path):
    data = tmp_path / "data"
    tdx = data / "tdx"
    tushare = data / "tushare"
    (tdx / "ts_daily_basic").mkdir(parents=True)
    (tdx / "ts_margin_detail").mkdir(parents=True)
    (tdx / "_manifest.json").write_text(json.dumps({
        "ts_daily_basic": {"done": True, "rows": 999},
        "ts_margin_detail": {"done": True, "rows": 999},
    }), encoding="utf-8")
    tushare.mkdir(parents=True)
    (tushare / "_manifest.json").write_text("{}", encoding="utf-8")
    _write_part(tdx / "ts_daily_basic" / "part_0000.parquet", [
        {"ts_code": "000001.SZ", "trade_date": "20261009", "total_mv": 1.0},
        {"ts_code": "000001.SZ", "trade_date": "20261009", "total_mv": 1.0},
    ])
    _write_part(tdx / "ts_margin_detail" / "part_0000.parquet", [
        {"ts_code": "000001.SZ", "trade_date": "20261009", "rzye": 2.0},
    ])
    result = build_full_refresh_baseline(data)
    daily = result["tables"]["ts_daily_basic"]
    assert daily["actual_rows"] == 2
    assert daily["manifest_rows"] == 999
    assert daily["duplicate_keys"] == 1
    assert daily["actual_latest"] == "20261009"
    assert daily["query_checks"]["count"] == 2
    assert daily["qualification"] == "blocked"


def test_baseline_infers_sector_and_holder_keys(tmp_path):
    data = tmp_path / "data"
    tdx = data / "tdx"
    (tdx / "tdx_sector_daily").mkdir(parents=True)
    (tdx / "tdx_sector_min").mkdir(parents=True)
    (tdx / "_manifest.json").write_text("{}", encoding="utf-8")
    _write_part(tdx / "tdx_sector_daily" / "part_0000.parquet", [
        {"code": "BK001", "trade_date": "20261009", "close": 1.0},
    ])
    _write_part(tdx / "tdx_sector_min" / "part_0000.parquet", [
        {"code": "BK001", "trade_date": "20261009", "trade_time": "09:31", "close": 1.0},
    ])
    result = build_full_refresh_baseline(data, tables=("tdx_sector_daily", "tdx_sector_min"))
    assert result["tables"]["tdx_sector_daily"]["primary_key"] == ["code", "trade_date"]
    assert result["tables"]["tdx_sector_min"]["primary_key"] == ["code", "trade_date", "trade_time"]


def test_baseline_uses_report_dimensions_for_financial_keys(tmp_path):
    data = tmp_path / "data"
    tushare = data / "tushare"
    (tushare / "income").mkdir(parents=True)
    (tushare / "top10_holders").mkdir(parents=True)
    (tushare / "_manifest.json").write_text("{}", encoding="utf-8")
    _write_part(tushare / "income" / "part_0000.parquet", [{
        "ts_code": "000001.SZ", "ann_date": "20261001", "f_ann_date": "20261002",
        "end_date": "20260930", "report_type": 1, "comp_type": 1, "end_type": 1,
    }])
    _write_part(tushare / "top10_holders" / "part_0000.parquet", [{
        "ts_code": "000001.SZ", "ann_date": "20261001", "end_date": "20260930",
        "holder_name": "甲", "holder_type": 1,
    }])
    result = build_full_refresh_baseline(data, tables=("income", "top10_holders"))
    assert result["tables"]["income"]["primary_key"] == [
        "ts_code", "ann_date", "end_date", "report_type", "comp_type", "end_type"
    ]
    assert result["tables"]["top10_holders"]["primary_key"] == [
        "ts_code", "ann_date", "end_date", "holder_name", "holder_type"
    ]
