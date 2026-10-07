import json
from pathlib import Path
import pandas as pd
from datetime import datetime
import data_integrity as integrity_module

from data_integrity import scan_integrity


def put(root, table, rows, source="tdx"):
    folder = root / source / table
    folder.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(folder / "part_0000.parquet", index=False)


def fixture(root):
    days = ["20260924", "20260928", "20260929", "20260930"]
    put(root, "trade_cal", [{"cal_date": d.strftime("%Y%m%d"), "exchange": "SSE",
                             "is_open": int(d.strftime("%Y%m%d") in days)}
                            for d in pd.date_range("20260924", "20261007")], "tushare")
    rows = [{"ts_code": c, "trade_date": d, "open": 1., "high": 2., "low": 1.,
             "close": 2., "vol": 10., "amount": 20.} for d in days for c in ["000001.SZ", "600519.SH"]]
    put(root, "ts_daily", rows)
    put(root, "ts_daily_basic", [{"ts_code": r["ts_code"], "trade_date": r["trade_date"],
                                 "turnover_rate": 1., "total_mv": 10., "circ_mv": 8., "pe": None} for r in rows])
    put(root, "tdx_sector_daily", [{"code": str(c), "trade_date": d, "open": 1., "high": 2.,
                                   "low": 1., "close": 2., "vol": 10., "amount": 20.}
                                  for d in days for c in range(10)])
    put(root, "ts_margin_detail", [{"ts_code": "600519.SH", "trade_date": d,
                                   "rzye": 1., "rzrqye": 1.} for d in days])
    put(root, "tdx_sector_min", [{"code": "1", "trade_date": d, "trade_time": "09:31", "open": 1., "high": 2.,
                                  "low": 1., "close": 2., "vol": 1., "amount": 2.} for d in days])
    put(root, "stock_basic", [{"ts_code": "600519.SH", "list_date": "20010827"},
                              {"ts_code": "000001.SZ", "list_date": "19910403"}], "tushare")
    (root / "tdx" / "_manifest.json").write_text(json.dumps({"tdx_sector_min": {"rows": 4, "parts": 1, "done": True}}))
    return rows


def metric(report, name):
    return next(m for m in report["metrics"] if m["name"] == name)


def test_key_difference_and_duplicate_rows_do_not_inflate_coverage(tmp_path):
    rows = fixture(tmp_path)
    put(tmp_path, "ts_daily_basic", [{"ts_code": r["ts_code"], "trade_date": r["trade_date"],
                                    "turnover_rate": 1., "total_mv": 10., "circ_mv": 8.}
                                   for r in rows if not (r["trade_date"] == "20260928" and r["ts_code"] == "600519.SH")])
    report = scan_integrity(tmp_path, today="20261007", run_probes=False)
    diff = metric(report, "daily_basic_key_reconciliation")
    assert diff["status"] == "FAIL"
    assert diff["details"]["20260928"]["missing"] == ["600519.SH"]
    assert metric(report, "ts_daily_basic_daily_coverage")["details"]["20260928"]["unique_codes"] == 1


def test_holiday_and_nullable_valuation_are_not_failures(tmp_path):
    fixture(tmp_path)
    report = scan_integrity(tmp_path, today="20261007", run_probes=False)
    assert metric(report, "ts_margin_detail_calendar_gaps")["status"] == "OK"
    assert metric(report, "ts_daily_basic_required_nulls")["status"] == "OK"
    assert metric(report, "ts_daily_basic_optional_nulls")["details"]["pe"] == 8


def test_old_margin_gap_sector_snapshot_and_required_null(tmp_path):
    rows = fixture(tmp_path)
    put(tmp_path, "ts_margin_detail", [{"ts_code": "600519.SH", "trade_date": d, "rzye": 1., "rzrqye": 1.}
                                       for d in ["20260924", "20260929", "20260930"]])
    put(tmp_path, "tdx_sector_daily", [{"code": str(c), "trade_date": d, "open": 1., "high": 2.,
                                       "low": 1., "close": 2., "vol": 10., "amount": 20.}
                                      for d in ["20260924", "20260928", "20260929", "20260930"]
                                      for c in range(1 if d == "20260930" else 10)])
    rows[-1]["close"] = None
    put(tmp_path, "ts_daily", rows)
    report = scan_integrity(tmp_path, today="20261007", run_probes=False)
    assert metric(report, "ts_margin_detail_calendar_gaps")["details"]["missing_days"] == ["20260928"]
    assert metric(report, "tdx_sector_daily_daily_coverage")["status"] == "FAIL"
    assert metric(report, "ts_daily_required_nulls")["status"] == "FAIL"


def test_manifest_does_not_hide_missing_minute_part(tmp_path):
    fixture(tmp_path)
    (tmp_path / "tdx" / "_manifest.json").write_text(json.dumps({"tdx_sector_min": {"rows": 40, "parts": 7, "done": True}}))
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
    assert metric(report, "ts_daily_required_nulls")["details"]["invalid_date_keys"] == 1


def test_calendar_truncation_cannot_make_tables_look_current(tmp_path):
    fixture(tmp_path)
    put(tmp_path, "trade_cal", [{"cal_date": "20260924", "exchange": "SSE", "is_open": 1}], "tushare")
    report = scan_integrity(tmp_path, today="20261007", run_probes=False)
    assert metric(report, "calendar")["status"] == "FAIL"


def test_duplicate_does_not_inflate_unique_stock_coverage(tmp_path):
    rows = fixture(tmp_path)
    put(tmp_path, "ts_daily", rows + [rows[-1]])
    report = scan_integrity(tmp_path, today="20261007", run_probes=False)
    assert metric(report, "ts_daily_duplicate_keys")["value"] == 1
    assert metric(report, "ts_daily_daily_coverage")["details"]["20260930"]["unique_codes"] == 2


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
    frame.loc[frame.trade_date.ne("20260930") | frame.code.eq("0")].to_parquet(path, index=False)
    entry = metric(scan_integrity(tmp_path, today="20261007", run_probes=False), "tdx_sector_daily_daily_coverage")
    assert entry["value"] == 9
    assert entry["details"]["20260930"]["missing_count"] == 9
    assert entry["details"]["_summary"]["latest_unique_codes"] == 1


def test_suspension_evidence_is_limited_to_verified_dates(tmp_path):
    fixture(tmp_path)
    path = tmp_path / "tdx" / "ts_daily" / "part_0000.parquet"
    frame = pd.read_parquet(path)
    frame.loc[frame.ts_code.ne("600519.SH") | frame.trade_date.ne("20260930")].to_parquet(path, index=False)
    row = {"ts_code": "600519.SH", "eligibility": "listed_full_day_suspended",
           "valid_start": "20260930", "valid_through": "20260930", "valid_dates": ["20260930"],
           "available_at": "2026-10-07T15:00:00+00:00", "sources": [
               {"provider": "tushare.suspend_d", "daily_rows": [{"ts_code": "600519.SH",
                "trade_date": "20260930", "suspend_type": "S", "suspend_timing": None}]},
               {"content_sha256": "a" * 64, "url": "https://example.test/notice", "excerpt": "全天停牌"}]}
    folder = tmp_path / "integrity"
    folder.mkdir()
    target = folder / "stock_daily_eligibility.json"
    target.write_text(json.dumps({"schema_version": 1, "records": [row]}), encoding="utf-8")
    entry = metric(scan_integrity(tmp_path, today="20261007", run_probes=False), "listed_stock_latest_coverage")
    assert entry["status"] == "OK" and entry["value"] == 0
    assert entry["details"]["observed"] == 1
    assert entry["details"]["eligible_suspended"] == ["600519.SH"]
    row["valid_dates"] = []
    target.write_text(json.dumps({"schema_version": 1, "records": [row]}), encoding="utf-8")
    entry = metric(scan_integrity(tmp_path, today="20261007", run_probes=False), "listed_stock_latest_coverage")
    assert entry["status"] == "WARN" and entry["value"] == 1


def test_small_sector_definition_gap_still_fails(tmp_path):
    fixture(tmp_path)
    put(tmp_path, "tdx_sector_def", [{"code": str(c)} for c in range(10)])
    path = tmp_path / "tdx" / "tdx_sector_daily" / "part_0000.parquet"
    frame = pd.read_parquet(path)
    frame.loc[frame.trade_date.ne("20260930") | frame.code.ne("9")].to_parquet(path, index=False)
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
    assert scan_integrity(tmp_path, today="20260930", run_probes=False)["expected_latest"] == "20260930"
