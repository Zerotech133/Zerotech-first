# -*- coding: utf-8 -*-
from datetime import date, datetime, timezone
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bar_dto import BarQuery
from bar_sources import LocalParquetBarSource, SourceReadError, TdxSectorBarSource
from bar_store import BarStore


def test_local_source_normalizes_and_preserves_zero_volume(tmp_path: Path):
    table = tmp_path / "stk_mins"
    table.mkdir()
    pd.DataFrame([{"ts_code": "000001.SZ", "trade_time": "2026-10-16 09:30:00", "open": 1, "high": 2, "low": 1, "close": 1.5, "vol": 0, "amount": None}]).to_parquet(table / "part.parquet", index=False)
    query = BarQuery(start=datetime(2026, 10, 16, 1, 29, tzinfo=timezone.utc), end=datetime(2026, 10, 16, 2, tzinfo=timezone.utc), as_of=datetime(2026, 10, 16, 2, tzinfo=timezone.utc))
    store = BarStore.from_source(LocalParquetBarSource(tmp_path, "stk_mins", "SZ"), query, symbol="000001.SZ")
    bars = store.all()
    assert len(bars) == 1 and bars[0].v == 0 and bars[0].source_trace == ("stk_mins",)
    assert bars[0].session_date == date(2026, 10, 16)
    assert bars[0].closed is False and bars[0].quality == "availability_estimated"
    assert store.explain()["sources"] == ["stk_mins"]


def test_pg_provider_uses_real_schema_and_closes_connection():
    class Cursor:
        description = [("symbol",), ("trade_time",), ("open",), ("high",), ("low",), ("close",), ("vol",), ("amount",), ("close_at",), ("available_at",), ("closed",), ("adjustment",), ("quality",)]
        def execute(self, sql, params):
            assert "bar_time" in sql and "turnover AS amount" in sql
        def fetchall(self):
            return [("US1", datetime(2026, 10, 16, 14, 30, tzinfo=timezone.utc), 1, 2, 1, 1.5, 0, 0, datetime(2026, 10, 16, 14, 31, tzinfo=timezone.utc), datetime(2026, 10, 16, 14, 31, tzinfo=timezone.utc), True, "none", "observed")]
        def __enter__(self): return self
        def __exit__(self, *args): return False
    class Connection:
        def cursor(self): return Cursor()
        def close(self): self.closed = True
    connection = Connection()
    query = BarQuery(start=datetime(2026, 10, 16, 14, tzinfo=timezone.utc), end=datetime(2026, 10, 16, 15, tzinfo=timezone.utc), as_of=datetime(2026, 10, 16, 15, tzinfo=timezone.utc))
    from bar_sources import PostgresBarSource
    bars = PostgresBarSource(lambda: connection).read(query, "US1", "US")
    assert bars[0].source_trace == ("v2_bar_minute",) and connection.closed


def test_local_source_accepts_naive_datetime_as_cn_local_time(tmp_path: Path):
    table = tmp_path / "stk_mins"
    table.mkdir()
    pd.DataFrame([{"ts_code": "000001.SZ", "trade_time": datetime(2026, 10, 16, 9, 30), "open": 1, "high": 2, "low": 1, "close": 1.5, "vol": 1}]).to_parquet(table / "part.parquet", index=False)
    query = BarQuery(start=datetime(2026, 10, 16, 1, 29, tzinfo=timezone.utc), end=datetime(2026, 10, 16, 2, tzinfo=timezone.utc), as_of=datetime(2026, 10, 16, 2, tzinfo=timezone.utc))
    bars = LocalParquetBarSource(tmp_path, "stk_mins", "SZ").read(query, symbol="000001.SZ")
    assert len(bars) == 1 and bars[0].dt == datetime(2026, 10, 16, 1, 30, tzinfo=timezone.utc)


def test_tdx_split_main_tail_merge_and_dedupe(tmp_path: Path):
    root = tmp_path / "data" / "tdx"
    (root / "tdx_sector_min").mkdir(parents=True)
    (root / "tdx_sector_min_tail").mkdir(parents=True)
    rows = [{"code": "880001", "trade_date": "20261016", "trade_time": "09:30", "open": 1, "high": 2, "low": 1, "close": 1.5, "vol": 0, "amount": 0}]
    pd.DataFrame(rows).to_parquet(root / "tdx_sector_min" / "part.parquet", index=False)
    pd.DataFrame(rows + [{"code": "880001", "trade_date": "20261016", "trade_time": "09:31", "open": 1.5, "high": 2, "low": 1.5, "close": 1.8, "vol": 1, "amount": 2}]).to_parquet(root / "tdx_sector_min_tail" / "part.parquet", index=False)
    query = BarQuery(start=datetime(2026, 10, 16, 1, 29, tzinfo=timezone.utc), end=datetime(2026, 10, 16, 1, 32, tzinfo=timezone.utc), as_of=datetime(2026, 10, 16, 2, tzinfo=timezone.utc))
    bars = TdxSectorBarSource(tmp_path).read(query, symbol="880001")
    assert len(bars) == 2 and bars[0].symbol == "880001" and bars[0].session_date == date(2026, 10, 16)
    assert bars[0].source_trace == ("tdx_sector_min", "tdx_sector_min_tail")
    assert bars[1].source_trace == ("tdx_sector_min_tail",)


def test_tdx_conflicting_duplicate_is_rejected(tmp_path: Path):
    root = tmp_path / "data" / "tdx" / "tdx_sector_min"
    root.mkdir(parents=True)
    row = {"code": "880001", "trade_date": "20261016", "trade_time": "09:30", "open": 1, "high": 2, "low": 1, "close": 1.5, "vol": 1, "amount": 1}
    other = dict(row, close=1.6)
    pd.DataFrame([row]).to_parquet(root / "a.parquet", index=False)
    pd.DataFrame([other]).to_parquet(root / "b.parquet", index=False)
    query = BarQuery(start=datetime(2026, 10, 16, 1, tzinfo=timezone.utc), end=datetime(2026, 10, 16, 2, tzinfo=timezone.utc), as_of=datetime(2026, 10, 16, 2, tzinfo=timezone.utc))
    with pytest.raises(SourceReadError, match="conflicting duplicate"):
        TdxSectorBarSource(tmp_path).read(query, symbol="880001")


def test_missing_source_is_explicit(tmp_path: Path):
    with pytest.raises(SourceReadError, match="no parquet source"):
        BarStore.from_source(LocalParquetBarSource(tmp_path, "idx_mins", "SSE"), BarQuery())
