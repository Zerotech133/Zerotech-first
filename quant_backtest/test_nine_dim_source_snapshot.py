# -*- coding: utf-8 -*-
"""九维 as-of 来源快照适配层回归。"""
import json
from pathlib import Path

import pytest

from nine_dim_source_snapshot import SourceSnapshot, SnapshotFormatError


def _write(path: Path, *, source_code=0, rows=None, asof="20261008"):
    rows = rows or []
    payload = {
        "schema_version": 1,
        "snapshot_type": "nine_dim_source_asof",
        "asof_trade_date": asof,
        "candidate_count": 1,
        "global_activation": False,
        "production_write": False,
        "source_receipts": {
            "per_code": {
                "605100.SH": {
                    "forecast": {
                        "request_params": {"ts_code": "605100.SH",
                                            "end_date": asof},
                        "source_code": source_code,
                        "fields": ["ts_code", "ann_date", "type"],
                        "rows": rows,
                    },
                    "report_rc": {"source_code": 0, "fields": [],
                                  "rows": []},
                    "block_trade": {"source_code": 0, "fields": [],
                                    "rows": []},
                }
            }
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_loads_rows_and_preserves_valid_empty(tmp_path):
    path = tmp_path / "snapshot.json"
    _write(path, rows=[["605100.SH", "20260715", "预增"]])
    snap = SourceSnapshot(path)

    record = snap.get("forecast", "605100.SH")

    assert record["source_status"] == "source_response_verified"
    assert record["coverage_status"] == "sample_has_data"
    assert record["rows"] == [["605100.SH", "20260715", "预增"]]
    assert snap.get("report_rc", "605100.SH")["coverage_status"] == \
        "valid_empty_response"


def test_missing_code_is_unknown_and_does_not_raise(tmp_path):
    path = tmp_path / "snapshot.json"
    _write(path)
    snap = SourceSnapshot(path)

    record = snap.get("forecast", "000001.SZ")

    assert record["source_status"] == "missing_receipt"
    assert record["coverage_status"] == "unknown"
    assert record["rows"] == []


def test_source_error_keeps_replay_unknown(tmp_path):
    path = tmp_path / "snapshot.json"
    _write(path, source_code=-2001)

    record = SourceSnapshot(path).get("forecast", "605100.SH")

    assert record["source_status"] == "source_error"
    assert record["err"] == 1


def test_rejects_production_enabled_snapshot(tmp_path):
    path = tmp_path / "snapshot.json"
    _write(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["production_write"] = True
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(SnapshotFormatError):
        SourceSnapshot(path)


def test_real_staging_asof_snapshot_is_metadata_safe():
    path = Path(__file__).parent / "data" / "tushare_staging" / \
        "nine_dim_source_asof_20261008.json"
    if not path.exists():
        pytest.skip("staging 快照未提供")

    snap = SourceSnapshot(path)

    assert len(snap.codes) == 218
    assert snap.get("forecast", "605100.SH")["source_status"] == \
        "source_response_verified"
    assert snap.get("forecast", "605100.SH")["payload_available"] is False
    assert snap.get("block_trade", "605100.SH")["coverage_status"] == \
        "valid_empty_response"


def test_real_payload_snapshot_exposes_rows_for_replay():
    path = Path(__file__).parent / "data" / "tushare_staging" / \
        "nine_dim_source_payload_20261008.json"
    if not path.exists():
        pytest.skip("staging 完整载荷未提供")

    snap = SourceSnapshot(path)
    record = snap.get("forecast", "605100.SH")

    assert len(snap.codes) == 218
    assert record["payload_available"] is True
    assert record["source_status"] == "source_response_verified"


def test_backtest_builds_api_record_stores_from_payload_snapshot():
    from nine_dim_backtest import _snapshot_stores

    path = Path(__file__).parent / "data" / "tushare_staging" / \
        "nine_dim_source_payload_20261008.json"
    if not path.exists():
        pytest.skip("staging 完整载荷未提供")

    stores = _snapshot_stores(path, ["605100.SH"])

    assert set(stores) == {"forecast", "report_rc", "block_trade"}
    assert stores["forecast"].get("605100.SH")["payload_available"] is True
    assert stores["block_trade"].get("605100.SH")["rows"] == []

