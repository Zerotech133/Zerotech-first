# -*- coding: utf-8 -*-
"""统一读层完整性回归；只使用临时目录，不接触生产 catalog。"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_catalog as bc
import datamart as dm


@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    def write(table, filename, rows, source="tushare"):
        store = tmp_path / "data" / source / table
        store.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_parquet(store / filename, index=False)

    def load():
        path = tmp_path / "catalog.json"
        cat = bc.build(data_root=str(tmp_path), out_path=str(path))
        monkeypatch.setattr(dm, "CATALOG", str(path))
        monkeypatch.setattr(dm, "_cat_cache", {"mtime": None, "data": None})
        return cat

    return write, load


def test_daily_alias_combines_complementary_dates_with_primary_priority(warehouse):
    write, load = warehouse
    write(
        "ts_daily",
        "part_0000.parquet",
        {
            "ts_code": ["600519.SH"] * 2,
            "trade_date": ["20230801", "20230802"],
            "close": [100, 101],
        },
        "tdx",
    )
    write(
        "daily",
        "d_20230801.parquet",
        {
            "ts_code": ["600519.SH"] * 2,
            "trade_date": ["20230731", "20230801"],
            "close": [99, 999],
        },
    )
    load()
    result = dm.read(
        "daily", start="20230731", end="20230802", codes=["600519.SH"], fields=["close"]
    )
    assert dict(zip(result.trade_date, result.close)) == {
        "20230731": 99,
        "20230801": 100,
        "20230802": 101,
    }


@pytest.mark.parametrize(
    "filename,code,other",
    [
        ("110075_SH.parquet", "110075.SH", "110075.SZ"),
        ("BTC_USDT.parquet", "BTC/USDT", "ETH/USDT"),
        ("BRK_B.parquet", "BRK.B", "BRK.A"),
        ("AAPL.parquet", "AAPL", "AAPLL"),
    ],
)
def test_exact_code_normalizes_filename_and_filters_content(
    warehouse, filename, code, other
):
    write, load = warehouse
    write(
        "t_codes",
        filename,
        {"ts_code": [code, other], "trade_date": ["20260105"] * 2, "close": [10, 999]},
    )
    load()
    result = dm.read("t_codes", codes=code, fields=["close"])
    assert result.ts_code.tolist() == [code]
    assert result.close.tolist() == [10]


def test_prefix_filters_rows_from_broad_filename_matches(warehouse):
    write, load = warehouse
    write(
        "t_codes",
        "600519_SH.parquet",
        {
            "ts_code": ["600519.SH", "600520.SH"],
            "trade_date": ["20260105"] * 2,
            "close": [10, 999],
        },
    )
    load()
    assert dm.read("t_codes", codes="600519").ts_code.tolist() == ["600519.SH"]


@pytest.mark.parametrize("table", ["top10_holders", "top10_floatholders"])
def test_period_holders_keep_each_holder_when_projecting_fields(warehouse, table):
    write, load = warehouse
    write(
        table,
        "p_20250331.parquet",
        {
            "ts_code": ["600519.SH"] * 3,
            "ann_date": ["20250430"] * 3,
            "end_date": ["20250331"] * 3,
            "holder_name": ["甲", "乙", "甲"],
            "hold_amount": [10, 20, 10],
        },
    )
    cat = load()
    result = dm.read(
        table,
        start="20250401",
        end="20250430",
        codes="600519.SH",
        fields=["hold_amount"],
    )
    assert result.hold_amount.tolist() == [10, 20]
    assert list(result.columns) == ["ts_code", "ann_date", "hold_amount"]
    assert cat["tables"][table]["layout"] == "by_period"
    assert dm.latest(table) == "20250430"


@pytest.mark.parametrize("table", ["income", "balancesheet", "cashflow"])
def test_financial_versions_survive_projection_and_coarse_fallback(warehouse, table):
    write, load = warehouse
    write(
        table,
        "600519_SH.parquet",
        {
            "ts_code": ["600519.SH"] * 3,
            "end_date": ["20250331"] * 3,
            "ann_date": ["20250430"] * 3,
            "f_ann_date": ["20250430"] * 3,
            "report_type": ["1", "2", "1"],
            "comp_type": ["1"] * 3,
            "end_type": ["1"] * 3,
            "update_flag": ["0"] * 3,
            "value": [10, 20, 10],
        },
    )
    write(
        "ts_" + table,
        "part_0000.parquet",
        {
            "ts_code": ["600519.SH"] * 2,
            "end_date": ["20250331", "20250630"],
            "ann_date": ["20250430", "20250830"],
            "value": [999, 30],
        },
        "tdx",
    )
    load()
    result = dm.read(table, codes="600519.SH", fields=["value"])
    assert sorted(result.value.tolist()) == [10, 20, 30]
    assert list(result.columns) == ["ts_code", "end_date", "value"]


def test_stock_basic_alias_uses_code_identity_and_complements_new_codes(warehouse):
    write, load = warehouse
    write(
        "ts_stock_basic",
        "part_0000.parquet",
        {"ts_code": ["600519.SH"], "list_date": ["20010827"], "name": ["主源"]},
        "tdx",
    )
    write(
        "stock_basic",
        "part_0000.parquet",
        {
            "ts_code": ["600519.SH", "001369.SZ"],
            "list_date": ["20010828", "20260930"],
            "name": ["次源", "新股"],
        },
    )
    load()
    result = dm.read("stock_basic", fields=["name"])
    assert dict(zip(result.ts_code, result.name)) == {
        "600519.SH": "主源",
        "001369.SZ": "新股",
    }
    assert len(result) == 2


def test_sqlite_event_normalization_preserves_same_day_events():
    df = pd.DataFrame(
        {
            "ts_code": ["600519.SH"] * 2,
            "notice_date": ["2026-01-05"] * 2,
            "title": ["公告甲", "公告乙"],
        }
    )
    result = dm._normalize(
        df,
        [
            {
                "layout": "sqlite",
                "code_col": "ts_code",
                "date_col": "notice_date",
                "primary_key": [],
            }
        ],
    )
    assert result.title.tolist() == ["公告甲", "公告乙"]


def test_legacy_sqlite_catalog_preserves_same_day_events():
    df = pd.DataFrame(
        {
            "ts_code": ["600519.SH"] * 2,
            "notice_date": ["2026-01-05"] * 2,
            "title": ["公告甲", "公告乙"],
        }
    )
    result = dm._normalize(
        df, [{"layout": "sqlite", "code_col": "ts_code", "date_col": "notice_date"}]
    )
    assert result.title.tolist() == ["公告甲", "公告乙"]


def test_missing_detail_identity_does_not_collapse_rows():
    df = pd.DataFrame(
        {
            "ts_code": ["600519.SH"] * 2,
            "ann_date": ["20260430"] * 2,
            "hold_amount": [10, 20],
        }
    )
    result = dm._normalize(
        df,
        [
            {
                "code_col": "ts_code",
                "date_col": "ann_date",
                "primary_key": ["ts_code", "ann_date", "holder_name"],
            }
        ],
    )
    assert result.hold_amount.tolist() == [10, 20]


def test_unready_primary_falls_back_and_limit_follows_merged_order(
    warehouse, monkeypatch
):
    write, load = warehouse
    write(
        "ts_daily",
        "part_0000.parquet",
        {"ts_code": ["600519.SH"], "trade_date": ["20230802"], "close": [101]},
        "tdx",
    )
    write(
        "daily",
        "d_20230801.parquet",
        {"ts_code": ["600519.SH"], "trade_date": ["20230801"], "close": [100]},
    )
    cat = load()
    assert dm.read("daily", limit=1).trade_date.tolist() == ["20230801"]
    cat["tables"]["ts_daily"]["done"] = False
    monkeypatch.setattr(dm, "_catalog", lambda: cat)
    assert dm.read("daily").close.tolist() == [100]


def test_holders_same_ann_date_different_period_and_type_survive(warehouse):
    write, load = warehouse
    write(
        "top10_holders",
        "p_20250331.parquet",
        {
            "ts_code": ["600519.SH"] * 3,
            "ann_date": ["20250430"] * 3,
            "end_date": ["20241231", "20250331", "20250331"],
            "holder_name": ["甲"] * 3,
            "holder_type": ["1", "1", "2"],
            "hold_amount": [10, 20, 30],
        },
    )
    load()
    assert dm.read(
        "top10_holders", codes="600519.SH", fields=["hold_amount"]
    ).hold_amount.tolist() == [10, 20, 30]


def test_schema_drift_keeps_projection_without_full_field_fallback(
    warehouse, monkeypatch
):
    write, load = warehouse
    write(
        "t_drift",
        "part_0000.parquet",
        {
            "ts_code": ["600519.SH"],
            "trade_date": ["20260105"],
            "close": [10],
            "unused": [999],
        },
    )
    write(
        "t_drift",
        "part_0001.parquet",
        {"ts_code": ["600519.SH"], "trade_date": ["20260106"], "unused": [888]},
    )
    load()
    original = pd.read_parquet
    calls = []

    def traced(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(pd, "read_parquet", traced)
    result = dm.read("t_drift", codes="600519.SH", fields=["close"])
    assert len(result) == 2 and result.close.iloc[0] == 10
    assert all(
        c.get("columns") is not None and "unused" not in c["columns"] for c in calls
    )


def test_minute_code_filter_and_dashed_date_bounds(warehouse):
    write, load = warehouse
    write(
        "stk_mins",
        "600519_SH_202601.parquet",
        {
            "ts_code": ["600519.SH", "600519.SH", "600520.SH", "600519.SH"],
            "trade_time": [
                "2026-01-04 09:30:00",
                "2026-01-05 09:30:00",
                "2026-01-05 09:30:00",
                "2026-01-06 09:30:00",
            ],
            "close": [1, 2, 999, 3],
        },
    )
    load()
    result = dm.read("stk_mins", codes="600519.SH", start="20260105", end="20260105")
    assert result.close.tolist() == [2]
    assert result.trade_time.tolist() == ["2026-01-05 09:30:00"]


def test_index_constituent_code_filter_preserves_existing_contract(warehouse):
    write, load = warehouse
    write(
        "ts_index_weight",
        "part_0000.parquet",
        {
            "ts_code": ["000300.SH"] * 2,
            "con_code": ["600519.SH", "600520.SH"],
            "trade_date": ["20260105"] * 2,
            "weight": [1, 2],
        },
        "tdx",
    )
    load()
    result = dm.read("ts_index_weight", codes="600519.SH", fields=["weight"])
    assert result.weight.tolist() == [1]
    assert result.ts_code.tolist() == ["000300.SH"]


@pytest.mark.parametrize("table", ["tdx_sector_min", "tdx_sector_min_tail"])
def test_sector_minutes_preserve_same_clock_time_on_different_days(warehouse, table):
    write, load = warehouse
    write(
        table,
        "part_0000.parquet",
        {
            "code": ["880001"] * 2,
            "trade_date": ["20260105", "20260106"],
            "trade_time": ["09:30:00"] * 2,
            "close": [10, 20],
        },
        "tdx",
    )
    load()
    result = dm.read(table, start="20260105", end="20260106", fields=["close"])
    assert result.close.tolist() == [10, 20]
    assert result.trade_date.tolist() == ["20260105", "20260106"]
    assert list(result.columns) == ["ts_code", "trade_date", "close"]


def test_trade_calendar_keeps_each_exchange_on_the_same_date(warehouse):
    write, load = warehouse
    write(
        "trade_cal",
        "part_0000.parquet",
        {"exchange": ["SSE", "SZSE"], "cal_date": ["20260105"] * 2, "is_open": [1, 1]},
    )
    load()
    result = dm.read("trade_cal", fields=["is_open"])
    assert result.is_open.tolist() == [1, 1]
    assert list(result.columns) == ["cal_date", "is_open"]


def test_alias_merge_identity_is_retained_during_field_projection(
    warehouse, monkeypatch
):
    write, load = warehouse
    write(
        "t_primary",
        "part_0000.parquet",
        {
            "ts_code": ["600519.SH"],
            "trade_date": ["20260105"],
            "event_id": ["a"],
            "value": [10],
        },
    )
    write(
        "t_secondary",
        "part_0000.parquet",
        {
            "ts_code": ["600519.SH"] * 2,
            "trade_date": ["20260105"] * 2,
            "event_id": ["a", "b"],
            "value": [999, 20],
        },
    )
    cat = load()
    cat["aliases"]["events"] = {
        "resolves": ["t_primary", "t_secondary"],
        "merge_key": ["ts_code", "event_id"],
    }
    for table in ("t_primary", "t_secondary"):
        cat["tables"][table]["primary_key"] = []
    monkeypatch.setattr(dm, "_catalog", lambda: cat)
    assert dm.read("events", fields=["value"]).value.tolist() == [10, 20]


@pytest.mark.parametrize("primary_empty", [False, True])
def test_event_alias_fallback_reads_next_source_only_when_empty(
    warehouse, monkeypatch, primary_empty
):
    write, load = warehouse
    write(
        "t_primary",
        "part_0000.parquet",
        {
            "ts_code": [] if primary_empty else ["600519.SH"] * 2,
            "trade_date": [] if primary_empty else ["20260105"] * 2,
            "value": [] if primary_empty else [10, 20],
        },
    )
    write(
        "t_secondary",
        "part_0000.parquet",
        {"ts_code": ["600519.SH"], "trade_date": ["20260105"], "value": [30]},
    )
    cat = load()
    cat["aliases"]["events"] = {
        "resolves": ["t_primary", "t_secondary"],
        "merge_key": [],
        "merge_mode": "fallback",
    }
    for table in ("t_primary", "t_secondary"):
        cat["tables"][table]["primary_key"] = []
    monkeypatch.setattr(dm, "_catalog", lambda: cat)
    original = dm._read_one
    calls = []

    def traced(table, *args, **kwargs):
        calls.append(table)
        return original(table, *args, **kwargs)

    monkeypatch.setattr(dm, "_read_one", traced)
    result = dm.read("events", fields=["value"])
    assert result.value.tolist() == ([30] if primary_empty else [10, 20])
    assert calls == (["t_primary", "t_secondary"] if primary_empty else ["t_primary"])


@pytest.mark.parametrize("explicit", [False, True])
def test_raw_code_alias_normalizes_merge_identity_and_keeps_priority(
    warehouse, monkeypatch, explicit
):
    write, load = warehouse
    write(
        "t_primary",
        "part_0000.parquet",
        {"code": ["880001"], "trade_date": ["20260105"], "close": [10]},
    )
    write(
        "t_secondary",
        "part_0000.parquet",
        {
            "code": ["880001"] * 2,
            "trade_date": ["20260105", "20260106"],
            "close": [999, 20],
        },
    )
    cat = load()
    alias = {"resolves": ["t_primary", "t_secondary"]}
    if explicit:
        alias["merge_key"] = ["code", "trade_date"]
    cat["aliases"]["sector"] = alias
    monkeypatch.setattr(dm, "_catalog", lambda: cat)
    result = dm.read("sector", fields=["close"])
    assert result.close.tolist() == [10, 20]
    assert result.ts_code.tolist() == ["880001", "880001"]


def test_catalog_first_shard_missing_holder_identity_keeps_later_details(warehouse):
    write, load = warehouse
    write(
        "top10_holders",
        "p_20250331.parquet",
        {
            "ts_code": ["600519.SH"] * 2,
            "ann_date": ["20250430"] * 2,
            "end_date": ["20250331"] * 2,
            "holder_type": ["1"] * 2,
            "hold_amount": [1, 2],
        },
    )
    write(
        "top10_holders",
        "p_20250630.parquet",
        {
            "ts_code": ["600519.SH"] * 2,
            "ann_date": ["20250830"] * 2,
            "end_date": ["20250630"] * 2,
            "holder_name": ["甲", "乙"],
            "holder_type": ["1"] * 2,
            "hold_amount": [10, 20],
        },
    )
    cat = load()
    assert "holder_name" in cat["tables"]["top10_holders"]["primary_key"]
    result = dm.read("top10_holders", codes="600519.SH", fields=["hold_amount"])
    assert result.hold_amount.tolist() == [1, 2, 10, 20]


def test_projection_reads_requested_field_added_in_later_shard(warehouse):
    write, load = warehouse
    write(
        "t_drift",
        "part_0000.parquet",
        {"ts_code": ["600519.SH"], "trade_date": ["20260105"], "unused": [999]},
    )
    write(
        "t_drift",
        "part_0001.parquet",
        {
            "ts_code": ["600519.SH"],
            "trade_date": ["20260106"],
            "new_field": [20],
            "unused": [888],
        },
    )
    load()
    result = dm.read("t_drift", fields=["new_field"])
    assert len(result) == 2 and result.new_field.dropna().tolist() == [20]


def test_query_pushes_projection_and_filters_to_parquet(warehouse, monkeypatch):
    write, load = warehouse
    write(
        "t_codes",
        "600519_SH.parquet",
        {
            "ts_code": ["600519.SH"] * 3,
            "trade_date": ["20260104", "20260105", "20260106"],
            "close": [1, 2, 3],
            "unused": [100, 200, 300],
        },
    )
    load()
    original = pd.read_parquet
    calls = []

    def traced(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(pd, "read_parquet", traced)
    result = dm.read(
        "t_codes", start="20260105", end="20260105", codes="600519.SH", fields=["close"]
    )
    assert result.close.tolist() == [2]
    assert calls and all(
        c.get("columns") == ["ts_code", "trade_date", "close"] and c.get("filters")
        for c in calls
    )
