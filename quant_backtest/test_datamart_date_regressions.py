"""读取范围与实时新鲜度回归；仅在临时仓生成真实 Parquet。"""

import importlib.util
import json
from pathlib import Path
import sys

import pandas as pd
import pytest


PROJECT = Path("C:/项目/老田的AI助手/quant_backtest")
sys.path.insert(0, str(PROJECT))
SPEC = importlib.util.spec_from_file_location(
    "datamart_date_candidate", Path(__file__).with_name("datamart.py")
)
dm = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dm)


@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    def configure(layout, files, freshness=None):
        for name, rows in files.items():
            pd.DataFrame(rows, columns=["ts_code", "trade_date", "close"]).to_parquet(
                tmp_path / name, index=False
            )
        catalog = tmp_path / "catalog.json"
        catalog.write_text(
            json.dumps(
                {
                    "tables": {
                        "test_dates": {
                            "store": str(tmp_path),
                            "layout": layout,
                            "done": True,
                            "code_col": "ts_code",
                            "date_col": "trade_date",
                            "primary_key": ["ts_code", "trade_date"],
                            "schema": ["ts_code", "trade_date", "close"],
                            "freshness": freshness,
                        }
                    },
                    "aliases": {},
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(dm, "CATALOG", str(catalog))
        monkeypatch.setattr(dm, "_cat_cache", {"mtime": None, "data": None})
        monkeypatch.setattr(dm, "_latest_cache", {})

    return configure


@pytest.mark.parametrize("first", [[], [["000001.SZ", "20260920", 1]]])
def test_latest_month_uses_all_codes_instead_of_first_filename(warehouse, first):
    warehouse(
        "by_code_month",
        {
            "000001_SZ_202609.parquet": first,
            "600519_SH_202609.parquet": [["600519.SH", "20260930", 2]],
        },
        freshness="20260901",
    )
    assert dm.latest("test_dates") == "20260930"


def test_latest_month_skips_all_empty_newer_months(warehouse):
    warehouse(
        "by_code_month",
        {
            "000001_SZ_202610.parquet": [],
            "600519_SH_202610.parquet": [],
            "600519_SH_202609.parquet": [["600519.SH", "20260930", 2]],
        },
        freshness="20260901",
    )
    assert dm.latest("test_dates") == "20260930"


@pytest.mark.parametrize(
    "layout,files",
    [
        ("by_code_month", {"600519_SH_202610.parquet": []}),
        ("by_date", {"d_20261001.parquet": []}),
    ],
)
def test_latest_empty_warehouse_has_no_actual_date(warehouse, layout, files):
    warehouse(layout, files)
    assert dm.latest("test_dates") is None


def test_latest_daily_skips_newer_empty_files(warehouse):
    warehouse(
        "by_date",
        {
            "d_20261001.parquet": [],
            "d_20260930.parquet": [["600519.SH", "20260930", 2]],
        },
        freshness="20260901",
    )
    assert dm.latest("test_dates") == "20260930"


def test_latest_daily_uses_actual_max_across_misnamed_files(warehouse):
    warehouse(
        "by_date",
        {
            "d_20260928.parquet": [["600519.SH", "20260930", 2]],
            "d_20260929.parquet": [["600519.SH", "20260929", 1]],
        },
    )
    assert dm.latest("test_dates") == "20260930"


def test_daily_reader_filters_mixed_date_formats_after_read(warehouse):
    warehouse(
        "by_date",
        {
            "d_20260105.parquet": [
                ["600519.SH", "2026-01-04", 1],
                ["600519.SH", "20260105", 2],
                ["000001.SZ", "2026-01-05", 3],
                ["600519.SH", "20260106", 4],
            ]
        },
    )
    result = dm.read("test_dates", start="20260105", end="20260105", fields=["close"])
    assert sorted(result.close.tolist()) == [2, 3]
    assert result.trade_date.tolist() == ["20260105", "20260105"]


def test_daily_reader_finds_requested_date_inside_differently_named_file(warehouse):
    warehouse(
        "by_date",
        {
            "d_20260106.parquet": [
                ["600519.SH", "20260105", 2],
                ["600519.SH", "20260106", 4],
            ]
        },
    )
    result = dm.read(
        "test_dates",
        codes="600519.SH",
        start="20260105",
        end="20260105",
        fields=["close"],
    )
    assert not result.empty
    assert result.close.tolist() == [2]
    assert result.trade_date.tolist() == ["20260105"]


@pytest.mark.parametrize(
    "start,end,expected",
    [
        (None, "20260105", [1, 2]),
        ("20260105", None, [2, 3]),
        ("20260105", "20260105", [2]),
    ],
)
def test_daily_reader_keeps_inclusive_open_date_bounds(warehouse, start, end, expected):
    warehouse(
        "by_date",
        {
            "d_20260105.parquet": [
                ["600519.SH", "20260104", 1],
                ["600519.SH", "20260105", 2],
                ["600519.SH", "20260106", 3],
            ]
        },
    )
    result = dm.read("test_dates", start=start, end=end)
    assert result.close.tolist() == expected
