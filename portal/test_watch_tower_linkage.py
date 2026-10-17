# -*- coding: utf-8 -*-
import datetime
from pathlib import Path

import pandas as pd

from watch_tower_linkage import LinkageReader


def test_linkage_reads_historical_sector_minutes(tmp_path: Path) -> None:
    tdx = tmp_path / "quant_backtest" / "data" / "tdx"
    (tdx / "tdx_sector_min").mkdir(parents=True)
    (tdx / "tdx_sector_def").mkdir(parents=True)
    (tdx / "ts_index_weight").mkdir(parents=True)
    (tdx / "tdx_sector_member").mkdir(parents=True)
    pd.DataFrame([{"code": "BK0001", "trade_date": "20261016", "trade_time": "09:31", "open": 1, "high": 2, "low": 1, "close": 1.5, "vol": 10, "amount": 20, "up_count": 2, "down_count": 1}]).to_parquet(tdx / "tdx_sector_min" / "part_0000.parquet", index=False)
    pd.DataFrame([{"code": "BK0001", "name": "测试行业", "seg": "industry"}]).to_parquet(tdx / "tdx_sector_def" / "part_0000.parquet", index=False)
    pd.DataFrame([{"index_code": "000300.SH", "con_code": "000001.SZ", "trade_date": "20261001", "weight": 1}]).to_parquet(tdx / "ts_index_weight" / "part_0000.parquet", index=False)
    pd.DataFrame([{"block_name": "测试行业", "con_code": "000001.SZ"}]).to_parquet(tdx / "tdx_sector_member" / "part_0000.parquet", index=False)
    result = LinkageReader(tmp_path).build(datetime.datetime(2026, 10, 16, tzinfo=datetime.timezone.utc), "000001.SZ")
    assert result["source_quality"] == "historical"
    assert result["minute_bars"][0]["code"] == "BK0001"
    assert result["relation"]["sector"] == ["测试行业"]
    assert result["relation"]["indexes"] == ["000300.SH"]


def test_linkage_missing_day_is_explicit(tmp_path: Path) -> None:
    tdx = tmp_path / "quant_backtest" / "data" / "tdx"
    (tdx / "tdx_sector_min").mkdir(parents=True)
    result = LinkageReader(tmp_path).build(datetime.datetime(2026, 10, 16, tzinfo=datetime.timezone.utc))
    assert result["source_quality"] == "unknown"
    assert result["minute_bars"] == []
    assert "no_historical_source" in result["gaps"]
