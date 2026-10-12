import json

import pytest

from gap_remediation import build_gap_snapshot, qualify_source, write_key_manifest


def _metric(name, details, value):
    return {"name": name, "status": "FAIL", "value": value, "details": details}


def _report():
    return {
        "integrity": {
            "metrics": [
                _metric(
                    "tdx_sector_daily_daily_coverage",
                    {
                        "20261001": {"missing": ["880002", "880001"], "missing_count": 2,
                                      "source_state": "source_empty_pending"},
                        "20260930": {"missing": ["880003"], "missing_count": 1},
                        "_summary": {"latest_missing_count": 0},
                    },
                    3,
                ),
                _metric(
                    "sector_minute_daily_coverage",
                    {"20261001": {"missing": ["880002"], "missing_count": 1}},
                    1,
                ),
                _metric(
                    "ts_margin_detail_daily_coverage",
                    {"20261001": {"missing": ["000001.SZ", "600000.SH"], "missing_count": 2}},
                    2,
                ),
                _metric(
                    "listed_stock_latest_coverage",
                    {"date": "20261001", "missing": ["000002.SZ"], "missing_expected_count": 1},
                    1,
                ),
            ]
        }
    }


def test_build_gap_snapshot_extracts_exact_sorted_keys_and_counts():
    snapshot = build_gap_snapshot(_report())

    assert snapshot["production_write"] is False
    assert snapshot["counts"] == {
        "tdx_sector_daily": 3,
        "tdx_sector_minute": 1,
        "ts_margin_detail": 2,
        "listed_stock": 1,
        "total": 7,
    }
    assert snapshot["keys"] == sorted(snapshot["keys"], key=lambda row: tuple(row[k] for k in ("table", "trade_date", "code")))
    assert snapshot["keys"][0] == {
        "table": "listed_stock",
        "source": "tushare",
        "market": "A",
        "code": "000002.SZ",
        "trade_date": "20261001",
        "status": "pending",
        "reason": "coverage_gap",
    }
    assert all(row["reason"] == "source_empty_pending"
               for row in snapshot["keys"] if row["table"] == "tdx_sector_daily" and row["trade_date"] == "20261001")


def test_build_gap_snapshot_is_deterministic_and_does_not_include_summary_rows():
    report = _report()
    first = build_gap_snapshot(report)
    second = build_gap_snapshot(report)
    assert first == second
    assert all(row["code"] != "_summary" for row in first["keys"])


def test_write_key_manifest_persists_json_and_production_guard(tmp_path):
    path = tmp_path / "gap.json"
    write_key_manifest(_report(), path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["production_write"] is False
    assert payload["counts"]["total"] == 7


def test_qualify_source_classifies_empty_and_accepts_valid_probe():
    keys = [{"table": "tdx_sector_daily", "code": "880001", "trade_date": "20261001"}]
    blocked = qualify_source("tdx_sector_daily", keys, lambda key: {"status": "source_empty"})
    assert blocked["counts"] == {"qualified": 0, "blocked": 1, "rejected": 0}
    assert blocked["keys"][0]["status"] == "blocked"
    assert blocked["keys"][0]["reason"] == "source_empty"

    valid = qualify_source("tdx_sector_daily", keys, lambda key: {"status": "candidate_ready", "row_count": 1})
    assert valid["counts"] == {"qualified": 1, "blocked": 0, "rejected": 0}
    assert valid["keys"][0]["status"] == "qualified"

