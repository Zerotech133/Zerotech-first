"""分钟覆盖与物理三键重复的离线回归。"""

import json

import pandas as pd
import pytest

from data_integrity import scan_integrity
from test_data_integrity import fixture, metric, put

DAYS = ["20260924", "20260928", "20260929", "20260930"]


def minute_row(code="0", day="20260930", time="09:31", **values):
    return {
        "code": code,
        "trade_date": day,
        "trade_time": time,
        "open": 0.0,
        "high": 0.0,
        "low": 0.0,
        "close": 0.0,
        "vol": 0.0,
        "amount": 0.0,
        **values,
    }


def minutes(root, rows, tail=None, second_part=None):
    put(root, "tdx_sector_min", rows)
    manifest = {"tdx_sector_min": {"rows": len(rows), "parts": 1}}
    if second_part is not None:
        pd.DataFrame(second_part).to_parquet(
            root / "tdx/tdx_sector_min/part_0001.parquet", index=False
        )
        manifest["tdx_sector_min"] = {"rows": len(rows) + len(second_part), "parts": 2}
    if tail is not None:
        put(root, "tdx_sector_min_tail", tail)
        manifest["tdx_sector_min_tail"] = {"rows": len(tail), "parts": 1}
    (root / "tdx/_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def scan(root):
    return scan_integrity(root, today="20261007", run_probes=False)


def test_defined_minute_single_gap_below_ten_percent_fails(tmp_path):
    fixture(tmp_path)
    put(tmp_path, "tdx_sector_def", [{"code": str(code)} for code in range(20)])
    rows = [
        minute_row(str(code), day)
        for day in DAYS
        for code in range(20)
        if (day, code) != ("20260930", 19)
    ]
    minutes(tmp_path, rows)
    entry = metric(scan(tmp_path), "sector_minute_daily_coverage")
    assert entry["status"] == "FAIL"
    assert entry["value"] == 1
    assert entry["details"]["20260930"]["missing"] == ["19"]
    assert entry["details"]["20260930"]["missing_count"] == 1


def test_defined_missing_is_not_hidden_by_equal_count_extra_code(tmp_path):
    fixture(tmp_path)
    put(tmp_path, "tdx_sector_def", [{"code": str(code)} for code in range(20)])
    rows = [
        minute_row("EXTRA" if day == "20260930" and code == 19 else str(code), day)
        for day in DAYS
        for code in range(20)
    ]
    minutes(tmp_path, rows)
    entry = metric(scan(tmp_path), "sector_minute_daily_coverage")
    assert entry["status"] == "FAIL"
    assert entry["value"] == 1
    assert entry["details"]["20260930"]["unique_codes"] == 20
    assert entry["details"]["20260930"]["missing"] == ["19"]


@pytest.mark.parametrize("duplicate_location", ["tail", "second_part", "same_part"])
def test_duplicate_minute_three_keys_fail_across_parts_and_tail(
    tmp_path, duplicate_location
):
    fixture(tmp_path)
    rows = [minute_row("0", day) for day in DAYS]
    duplicate = minute_row("0", "20260930", close=99.0)
    minutes(
        tmp_path,
        rows + ([duplicate] if duplicate_location == "same_part" else []),
        tail=[duplicate] if duplicate_location == "tail" else None,
        second_part=[duplicate] if duplicate_location == "second_part" else None,
    )
    report = scan(tmp_path)
    assert metric(report, "sector_minute_manifest")["status"] == "OK"
    entry = metric(report, "sector_minute_duplicate_keys")
    assert entry["status"] == "FAIL"
    assert entry["value"] == 1
    assert entry["details"]["20260930"] == 1


def test_distinct_minute_time_date_and_code_are_not_duplicates(tmp_path):
    fixture(tmp_path)
    rows = [minute_row("0", day) for day in DAYS]
    minutes(
        tmp_path,
        rows,
        tail=[
            minute_row("0", "20260930", "09:32"),
            minute_row("1", "20260930", "09:31"),
        ],
    )
    report = scan(tmp_path)
    entry = metric(report, "sector_minute_duplicate_keys")
    assert entry["status"] == "OK" and entry["value"] == 0
    assert metric(report, "sector_minute_required_nulls")["status"] == "OK"


def test_null_minute_key_stays_unknown_and_not_counted_as_duplicate(tmp_path):
    fixture(tmp_path)
    rows = [minute_row("0", day) for day in DAYS]
    minutes(tmp_path, rows + [minute_row(None), minute_row(None)])
    report = scan(tmp_path)
    assert metric(report, "sector_minute_required_nulls")["status"] == "FAIL"
    assert metric(report, "sector_minute_required_nulls")["details"]["code"] == 2
    assert metric(report, "sector_minute_duplicate_keys")["value"] == 0


def test_without_definition_keeps_reference_scope_and_reports_missing_count(tmp_path):
    fixture(tmp_path)
    rows = [
        minute_row(str(code), day)
        for day in DAYS
        for code in range(20)
        if (day, code) != ("20260930", 19)
    ]
    minutes(tmp_path, rows)
    entry = metric(scan(tmp_path), "sector_minute_daily_coverage")
    assert entry["status"] == "OK"
    assert entry["value"] == 1
    assert entry["details"]["20260930"]["missing"] == []
    assert entry["details"]["20260930"]["missing_count"] == 1
    assert entry["details"]["_summary"]["reference_source"] == "recent_max_unique_count"


def test_complete_defined_coverage_value_is_zero_not_baseline_size(tmp_path):
    fixture(tmp_path)
    put(tmp_path, "tdx_sector_def", [{"code": "0"}])
    minutes(tmp_path, [minute_row("0", day) for day in DAYS])
    entry = metric(scan(tmp_path), "sector_minute_daily_coverage")
    assert entry["status"] == "OK" and entry["value"] == 0
