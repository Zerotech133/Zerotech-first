import json
import pandas as pd
from datetime import datetime
import data_integrity as integrity_module
import pytest

from data_integrity import scan_integrity


def put(root, table, rows, source="tdx"):
    folder = root / source / table
    folder.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(folder / "part_0000.parquet", index=False)


def fixture(root):
    days = ["20260924", "20260928", "20260929", "20260930"]
    put(
        root,
        "trade_cal",
        [
            {
                "cal_date": d.strftime("%Y%m%d"),
                "exchange": "SSE",
                "is_open": int(d.strftime("%Y%m%d") in days),
            }
            for d in pd.date_range("20260924", "20261007")
        ],
        "tushare",
    )
    rows = [
        {
            "ts_code": c,
            "trade_date": d,
            "open": 1.0,
            "high": 2.0,
            "low": 1.0,
            "close": 2.0,
            "vol": 10.0,
            "amount": 20.0,
        }
        for d in days
        for c in ["000001.SZ", "600519.SH"]
    ]
    put(root, "ts_daily", rows)
    put(
        root,
        "ts_daily_basic",
        [
            {
                "ts_code": r["ts_code"],
                "trade_date": r["trade_date"],
                "turnover_rate": 1.0,
                "total_mv": 10.0,
                "circ_mv": 8.0,
                "pe": None,
            }
            for r in rows
        ],
    )
    put(
        root,
        "tdx_sector_daily",
        [
            {
                "code": str(c),
                "trade_date": d,
                "open": 1.0,
                "high": 2.0,
                "low": 1.0,
                "close": 2.0,
                "vol": 10.0,
                "amount": 20.0,
            }
            for d in days
            for c in range(10)
        ],
    )
    put(
        root,
        "ts_margin_detail",
        [
            {"ts_code": "600519.SH", "trade_date": d, "rzye": 1.0, "rzrqye": 1.0}
            for d in days
        ],
    )
    put(
        root,
        "tdx_sector_min",
        [
            {
                "code": "1",
                "trade_date": d,
                "trade_time": "09:31",
                "open": 1.0,
                "high": 2.0,
                "low": 1.0,
                "close": 2.0,
                "vol": 1.0,
                "amount": 2.0,
            }
            for d in days
        ],
    )
    put(
        root,
        "stock_basic",
        [
            {"ts_code": "600519.SH", "list_date": "20010827"},
            {"ts_code": "000001.SZ", "list_date": "19910403"},
        ],
        "tushare",
    )
    (root / "tdx" / "_manifest.json").write_text(
        json.dumps({"tdx_sector_min": {"rows": 4, "parts": 1, "done": True}})
    )
    return rows


def metric(report, name):
    return next(m for m in report["metrics"] if m["name"] == name)


def test_key_difference_and_duplicate_rows_do_not_inflate_coverage(tmp_path):
    rows = fixture(tmp_path)
    put(
        tmp_path,
        "ts_daily_basic",
        [
            {
                "ts_code": r["ts_code"],
                "trade_date": r["trade_date"],
                "turnover_rate": 1.0,
                "total_mv": 10.0,
                "circ_mv": 8.0,
            }
            for r in rows
            if not (r["trade_date"] == "20260928" and r["ts_code"] == "600519.SH")
        ],
    )
    report = scan_integrity(tmp_path, today="20261007", run_probes=False)
    diff = metric(report, "daily_basic_key_reconciliation")
    assert diff["status"] == "FAIL"
    assert diff["details"]["20260928"]["missing"] == ["600519.SH"]
    assert (
        metric(report, "ts_daily_basic_daily_coverage")["details"]["20260928"][
            "unique_codes"
        ]
        == 1
    )


def test_holiday_and_nullable_valuation_are_not_failures(tmp_path):
    fixture(tmp_path)
    report = scan_integrity(tmp_path, today="20261007", run_probes=False)
    assert metric(report, "ts_margin_detail_calendar_gaps")["status"] == "OK"
    assert metric(report, "ts_daily_basic_required_nulls")["status"] == "OK"
    assert metric(report, "ts_daily_basic_optional_nulls")["details"]["pe"] == 8


def test_old_margin_gap_sector_snapshot_and_required_null(tmp_path):
    rows = fixture(tmp_path)
    put(
        tmp_path,
        "ts_margin_detail",
        [
            {"ts_code": "600519.SH", "trade_date": d, "rzye": 1.0, "rzrqye": 1.0}
            for d in ["20260924", "20260929", "20260930"]
        ],
    )
    put(
        tmp_path,
        "tdx_sector_daily",
        [
            {
                "code": str(c),
                "trade_date": d,
                "open": 1.0,
                "high": 2.0,
                "low": 1.0,
                "close": 2.0,
                "vol": 10.0,
                "amount": 20.0,
            }
            for d in ["20260924", "20260928", "20260929", "20260930"]
            for c in range(1 if d == "20260930" else 10)
        ],
    )
    rows[-1]["close"] = None
    put(tmp_path, "ts_daily", rows)
    report = scan_integrity(tmp_path, today="20261007", run_probes=False)
    assert metric(report, "ts_margin_detail_calendar_gaps")["details"][
        "missing_days"
    ] == ["20260928"]
    assert metric(report, "tdx_sector_daily_daily_coverage")["status"] == "FAIL"
    assert metric(report, "ts_daily_required_nulls")["status"] == "FAIL"


def test_manifest_does_not_hide_missing_minute_part(tmp_path):
    fixture(tmp_path)
    (tmp_path / "tdx" / "_manifest.json").write_text(
        json.dumps({"tdx_sector_min": {"rows": 40, "parts": 7, "done": True}})
    )
    report = scan_integrity(tmp_path, today="20261007", run_probes=False)
    assert metric(report, "sector_minute_manifest")["status"] == "FAIL"


def test_query_failure_is_reported(tmp_path):
    fixture(tmp_path)

    class Reader:
        def read(self, *args, **kwargs):
            raise ValueError("查询路径错误")

    report = scan_integrity(tmp_path, today="20261007", reader=Reader())
    assert metric(report, "query_daily_history")["status"] == "FAIL"
    assert "查询路径错误" in metric(report, "query_daily_history")["details"]["error"]


def test_default_query_reader_uses_explicit_data_root(tmp_path):
    """显式 data_root 时，默认查询探针必须读取同一数据根。"""
    fixture(tmp_path)
    days = [
        "20230720",
        "20230721",
        "20230724",
        "20230725",
        "20230726",
        "20230727",
        "20230728",
        "20230731",
        "20230801",
        "20230802",
        "20230803",
        "20230804",
    ]
    daily_dir = tmp_path / "tushare" / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [{"ts_code": "600519.SH", "trade_date": day, "close": 1.0} for day in days]
    ).to_parquet(daily_dir / "d_202307.parquet", index=False)
    put(
        tmp_path,
        "cb_daily",
        [{"ts_code": "110075.SH", "trade_date": "20260901", "close": 1.0}],
        "tushare",
    )
    (tmp_path / "tushare" / "cb_daily" / "part_0000.parquet").rename(
        tmp_path / "tushare" / "cb_daily" / "110075_SH.parquet"
    )
    put(
        tmp_path,
        "income",
        [
            {
                "ts_code": "600519.SH",
                "ann_date": "20260901",
                "end_date": "20260630",
                "value": 1.0,
            }
        ],
        "tushare",
    )
    (tmp_path / "tushare" / "income" / "part_0000.parquet").rename(
        tmp_path / "tushare" / "income" / "600519_SH.parquet"
    )
    for table in ("top10_holders", "top10_floatholders"):
        put(
            tmp_path,
            table,
            [
                {
                    "ts_code": "600519.SH",
                    "ann_date": "20260901",
                    "end_date": "20260630",
                    "holder_name": "甲",
                    "holder_type": "1",
                    "hold_amount": 1.0,
                }
            ],
            "tushare",
        )
        (tmp_path / "tushare" / table / "part_0000.parquet").rename(
            tmp_path / "tushare" / table / "p_20260630.parquet"
        )
    tables = {}
    for name, layout, date_col, key in [
        ("daily", "by_date", "trade_date", ["ts_code", "trade_date"]),
        ("cb_daily", "by_code", "trade_date", ["ts_code", "trade_date"]),
        ("income", "by_code", "end_date", ["ts_code", "end_date"]),
        (
            "top10_holders",
            "by_period",
            "ann_date",
            ["ts_code", "ann_date", "end_date", "holder_name", "holder_type"],
        ),
        (
            "top10_floatholders",
            "by_period",
            "ann_date",
            ["ts_code", "ann_date", "end_date", "holder_name", "holder_type"],
        ),
    ]:
        tables[name] = {
            "store": str(tmp_path / "tushare" / name),
            "layout": layout,
            "date_col": date_col,
            "code_col": "ts_code",
            "primary_key": key,
            "done": True,
            "schema": [],
        }
    (tmp_path / "catalog.json").write_text(
        json.dumps({"tables": tables, "aliases": {}}, ensure_ascii=False),
        encoding="utf-8",
    )
    report = scan_integrity(tmp_path, today="20261007")
    assert metric(report, "query_daily_history")["status"] == "OK"
    assert metric(report, "query_cb_daily")["status"] == "OK"


def test_corrupt_table_is_explicit_failure(tmp_path):
    fixture(tmp_path)
    (tmp_path / "tdx" / "ts_daily" / "part_0000.parquet").write_bytes(b"bad parquet")
    report = scan_integrity(tmp_path, today="20261007", run_probes=False)
    assert metric(report, "ts_daily_scan")["status"] == "FAIL"


def test_invalid_date_key_is_not_hidden_by_recent_window(tmp_path):
    rows = fixture(tmp_path)
    put(tmp_path, "ts_daily", rows + [dict(rows[-1], trade_date="", close=None)])
    report = scan_integrity(tmp_path, today="20261007", run_probes=False)
    assert metric(report, "ts_daily_required_nulls")["status"] == "FAIL"
    assert (
        metric(report, "ts_daily_required_nulls")["details"]["invalid_date_keys"] == 1
    )


def test_calendar_truncation_cannot_make_tables_look_current(tmp_path):
    fixture(tmp_path)
    put(
        tmp_path,
        "trade_cal",
        [{"cal_date": "20260924", "exchange": "SSE", "is_open": 1}],
        "tushare",
    )
    report = scan_integrity(tmp_path, today="20261007", run_probes=False)
    assert metric(report, "calendar")["status"] == "FAIL"


def test_duplicate_does_not_inflate_unique_stock_coverage(tmp_path):
    rows = fixture(tmp_path)
    put(tmp_path, "ts_daily", rows + [rows[-1]])
    report = scan_integrity(tmp_path, today="20261007", run_probes=False)
    assert metric(report, "ts_daily_duplicate_keys")["value"] == 1
    assert (
        metric(report, "ts_daily_daily_coverage")["details"]["20260930"]["unique_codes"]
        == 2
    )


def test_minute_market_columns_are_checked(tmp_path):
    fixture(tmp_path)
    path = tmp_path / "tdx" / "tdx_sector_min" / "part_0000.parquet"
    frame = pd.read_parquet(path)
    frame.loc[0, "close"] = None
    frame.to_parquet(path, index=False)
    report = scan_integrity(tmp_path, today="20261007", run_probes=False)
    assert metric(report, "sector_minute_required_nulls")["status"] == "FAIL"


def test_internal_calendar_hole_is_reported(tmp_path):
    fixture(tmp_path)
    path = tmp_path / "tushare" / "trade_cal" / "part_0000.parquet"
    frame = pd.read_parquet(path)
    frame.loc[frame.cal_date.ne("20260928")].to_parquet(path, index=False)
    report = scan_integrity(tmp_path, today="20261007", run_probes=False)
    assert metric(report, "calendar")["status"] == "FAIL"


def test_coverage_value_reports_missing_keys_instead_of_reference_size(tmp_path):
    fixture(tmp_path)
    path = tmp_path / "tdx" / "tdx_sector_daily" / "part_0000.parquet"
    frame = pd.read_parquet(path)
    frame.loc[frame.trade_date.ne("20260930") | frame.code.eq("0")].to_parquet(
        path, index=False
    )
    entry = metric(
        scan_integrity(tmp_path, today="20261007", run_probes=False),
        "tdx_sector_daily_daily_coverage",
    )
    assert entry["value"] == 9
    assert entry["details"]["20260930"]["missing_count"] == 9
    assert entry["details"]["_summary"]["latest_unique_codes"] == 1


def test_coverage_records_window_reference_and_stable_gap_evidence(tmp_path):
    fixture(tmp_path)
    path = tmp_path / "tdx" / "tdx_sector_daily" / "part_0000.parquet"
    frame = pd.read_parquet(path)
    frame.loc[frame.trade_date.ne("20260930")].to_parquet(path, index=False)
    entry = metric(
        scan_integrity(tmp_path, today="20261007", run_probes=False),
        "tdx_sector_daily_daily_coverage",
    )
    summary = entry["details"]["_summary"]
    assert summary["window_start"] == "20260924"
    assert summary["window_end"] == "20260930"
    assert summary["reference_date"] == "20260929"
    assert len(summary["gap_keys"]["missing_keys_sample"]) == 10
    assert len(summary["gap_keys"]["missing_keys_sha256"]) == 64


def test_empty_day_is_marked_as_source_pending(tmp_path):
    fixture(tmp_path)
    path = tmp_path / "tdx" / "ts_daily" / "part_0000.parquet"
    frame = pd.read_parquet(path)
    frame.loc[frame.trade_date.ne("20260930")].to_parquet(path, index=False)
    entry = metric(
        scan_integrity(tmp_path, today="20261007", run_probes=False),
        "ts_daily_daily_coverage",
    )
    assert entry["details"]["20260930"]["source_state"] == "source_empty_pending"


def test_suspension_evidence_is_limited_to_verified_dates(tmp_path):
    fixture(tmp_path)
    path = tmp_path / "tdx" / "ts_daily" / "part_0000.parquet"
    frame = pd.read_parquet(path)
    frame.loc[
        frame.ts_code.ne("600519.SH") | frame.trade_date.ne("20260930")
    ].to_parquet(path, index=False)
    row = {
        "ts_code": "600519.SH",
        "eligibility": "listed_full_day_suspended",
        "valid_start": "20260930",
        "valid_through": "20260930",
        "valid_dates": ["20260930"],
        "available_at": "2026-10-07T15:00:00+00:00",
        "sources": [
            {
                "provider": "tushare.suspend_d",
                "daily_rows": [
                    {
                        "ts_code": "600519.SH",
                        "trade_date": "20260930",
                        "suspend_type": "S",
                        "suspend_timing": None,
                    }
                ],
            },
            {
                "content_sha256": "a" * 64,
                "url": "https://example.test/notice",
                "excerpt": "全天停牌",
            },
        ],
    }
    folder = tmp_path / "integrity"
    folder.mkdir()
    target = folder / "stock_daily_eligibility.json"
    target.write_text(
        json.dumps({"schema_version": 1, "records": [row]}), encoding="utf-8"
    )
    entry = metric(
        scan_integrity(tmp_path, today="20261007", run_probes=False),
        "listed_stock_latest_coverage",
    )
    assert entry["status"] == "OK" and entry["value"] == 0
    assert entry["details"]["observed"] == 1
    assert entry["details"]["eligible_suspended"] == ["600519.SH"]
    row["valid_dates"] = []
    target.write_text(
        json.dumps({"schema_version": 1, "records": [row]}), encoding="utf-8"
    )
    entry = metric(
        scan_integrity(tmp_path, today="20261007", run_probes=False),
        "listed_stock_latest_coverage",
    )
    assert entry["status"] == "WARN" and entry["value"] == 1


def test_small_sector_definition_gap_still_fails(tmp_path):
    fixture(tmp_path)
    put(tmp_path, "tdx_sector_def", [{"code": str(c)} for c in range(10)])
    path = tmp_path / "tdx" / "tdx_sector_daily" / "part_0000.parquet"
    frame = pd.read_parquet(path)
    frame.loc[frame.trade_date.ne("20260930") | frame.code.ne("9")].to_parquet(
        path, index=False
    )
    report = scan_integrity(tmp_path, today="20261007", run_probes=False)
    entry = metric(report, "sector_definition_key_reconciliation")
    assert entry["value"] == 1
    assert entry["status"] == "FAIL"
    assert metric(report, "tdx_sector_daily_daily_coverage")["status"] == "FAIL"


def test_intraday_checks_use_last_completed_session(tmp_path, monkeypatch):
    fixture(tmp_path)

    class Clock(datetime):
        hour = 10

        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 30, cls.hour, 0, tzinfo=tz)

    monkeypatch.setattr(integrity_module, "datetime", Clock)
    report = scan_integrity(tmp_path, run_probes=False)
    assert report["expected_latest"] == "20260929"
    Clock.hour = 16
    report = scan_integrity(tmp_path, run_probes=False)
    assert report["expected_latest"] == "20260930"
    Clock.hour = 10
    assert (
        scan_integrity(tmp_path, today="20260930", run_probes=False)["expected_latest"]
        == "20260930"
    )


def test_margin_earliest_date_from_all_local_parts_and_roots(tmp_path):
    put(
        tmp_path,
        "stock_basic",
        [{"ts_code": "NEW.SZ", "list_date": "20260929"}],
        "tushare",
    )
    path = tmp_path / "tushare/stock_basic/part_0001.parquet"
    pd.DataFrame([{"ts_code": "NEW.SZ", "list_date": "20260928"}]).to_parquet(
        path, index=False
    )
    put(tmp_path, "ts_stock_basic", [{"ts_code": "NEW.SZ", "list_date": "2026-09-24"}])
    dates, sources, invalid = integrity_module._stock_list_dates(tmp_path)
    assert dates == {"NEW.SZ": "20260924"}
    assert len(sources) == 3 and invalid == 0
    keys = {"20260924": {"OLD.SH"}, "20260929": {"OLD.SH", "NEW.SZ"}}
    details, value, failed = integrity_module._margin_coverage(
        tmp_path, keys, {d: len(v) for d, v in keys.items()}, sorted(keys), "20260929"
    )
    assert failed and value == 1
    assert details["20260924"]["missing"] == ["NEW.SZ"]


@pytest.mark.parametrize(
    "date", [None, "", "20260230", "20261301", "2026092", "unknown"]
)
def test_margin_unknown_invalid_dates_cannot_exempt_missing(tmp_path, date):
    put(tmp_path, "stock_basic", [{"ts_code": "NEW.SZ", "list_date": date}], "tushare")
    keys = {"20260924": {"OLD.SH"}, "20260929": {"OLD.SH", "NEW.SZ"}}
    details, value, failed = integrity_module._margin_coverage(
        tmp_path, keys, {d: len(v) for d, v in keys.items()}, sorted(keys), "20260929"
    )
    assert failed and value == 1
    assert details["20260924"]["unknown_list_date_missing"] == ["NEW.SZ"]


def test_margin_equal_counts_extra_cannot_hide_missing_and_latest_tie(tmp_path):
    keys = {"20260924": {"OLD.SH", "EXTRA.SH"}, "20260929": {"OLD.SH", "NEW.SZ"}}
    details, value, failed = integrity_module._margin_coverage(
        tmp_path, keys, {d: 2 for d in keys}, sorted(keys), "20260929"
    )
    assert value == 1 and failed
    assert details["_summary"]["reference_date"] == "20260929"
    assert details["_summary"]["raw_count"] == 0 and details["_summary"]["raw_set"] == 1
    assert details["20260924"]["historical_extra"] == ["EXTRA.SH"]


def test_margin_prelisting_present_fails_even_without_missing(tmp_path):
    put(
        tmp_path,
        "stock_basic",
        [{"ts_code": "NEW.SZ", "list_date": "20260929"}],
        "tushare",
    )
    keys = {"20260924": {"NEW.SZ"}, "20260929": {"NEW.SZ"}}
    details, value, failed = integrity_module._margin_coverage(
        tmp_path, keys, {d: 1 for d in keys}, sorted(keys), "20260929"
    )
    assert failed and value == 0
    assert details["_summary"]["prelisting_present"] == [
        {"ts_code": "NEW.SZ", "trade_date": "20260924", "list_date": "20260929"}
    ]


def test_margin_prelisting_excluded_but_small_real_gap_still_fails(tmp_path):
    fixture(tmp_path)
    codes = {"C%03d.SH" % i for i in range(20)}
    put(
        tmp_path,
        "ts_stock_basic",
        [{"ts_code": code, "list_date": "19900101"} for code in codes]
        + [{"ts_code": "NEW.SZ", "list_date": "20260929"}],
    )
    days = ["20260924", "20260928", "20260929", "20260930"]
    keys = {day: codes | ({"NEW.SZ"} if day >= "20260929" else set()) for day in days}
    keys["20260930"] = keys["20260930"] - {"C000.SH"}
    put(
        tmp_path,
        "ts_margin_detail",
        [
            {"ts_code": code, "trade_date": day, "rzye": 1.0, "rzrqye": 1.0}
            for day, members in keys.items()
            for code in members
        ],
    )
    entry = metric(
        scan_integrity(tmp_path, today="20261007", run_probes=False),
        "ts_margin_detail_daily_coverage",
    )
    assert entry["status"] == "FAIL" and entry["value"] == 1
    assert entry["details"]["_summary"]["raw_set"] == 3
    assert entry["details"]["_summary"]["prelisting_excluded_count"] == 2


def test_verified_17_codes_158_prelisting_keys_use_only_project_stock_dates(tmp_path):
    root = tmp_path
    expected = {
        "301686.SZ": "20260922",
        "301689.SZ": "20260910",
        "301699.SZ": "20260909",
        "301716.SZ": "20260929",
        "601091.SH": "20260917",
        "603448.SH": "20260907",
        "688801.SH": "20260911",
        "688837.SH": "20260916",
        "920025.BJ": "20260923",
        "920071.BJ": "20260903",
        "920201.BJ": "20260924",
        "920202.BJ": "20260929",
        "920229.BJ": "20260922",
        "920268.BJ": "20260909",
        "920269.BJ": "20260907",
        "920289.BJ": "20260904",
        "920298.BJ": "20260916",
    }
    days = [
        "20260902",
        "20260903",
        "20260904",
        "20260907",
        "20260908",
        "20260909",
        "20260910",
        "20260911",
        "20260914",
        "20260915",
        "20260916",
        "20260917",
        "20260918",
        "20260921",
        "20260922",
        "20260923",
        "20260924",
        "20260928",
        "20260929",
    ]
    rows = [{"ts_code": code, "list_date": listed} for code, listed in expected.items()]
    put(root, "stock_basic", rows[:8], "tushare")
    put(root, "ts_stock_basic", rows[8:])
    dates, sources, _ = integrity_module._stock_list_dates(root)
    assert {code: dates[code] for code in expected} == expected
    keys = {
        day: {"OLD.SH"} | {code for code, listed in expected.items() if day >= listed}
        for day in days
    }
    details, value, failed = integrity_module._margin_coverage(
        root, keys, {day: len(codes) for day, codes in keys.items()}, days, "20260929"
    )
    assert (
        len(expected) == 17 and details["_summary"]["prelisting_excluded_count"] == 158
    )
    assert value == 0 and not failed
    assert all(str(root) in row["path"] for row in sources)
