import json

import pytest

from sector_gap_backfill import (
    reconcile_sector_definition,
    stage_sector_daily,
    stage_sector_daily_by_date,
    validate_sector_daily_response,
)


def key(code="880001", date="20261001"):
    return {"code": code, "trade_date": date}


def reply(rows=None, total=None):
    rows = rows if rows is not None else [["880001", "20261001", 1, 2, 1, 2, 10, 20]]
    return {
        "total": len(rows) if total is None else total,
        "columns": ["code", "trade_date", "open", "high", "low", "close", "vol", "amount"],
        "rows": rows,
    }


def test_probe_validates_one_matching_row():
    result = validate_sector_daily_response(key(), reply())
    assert result["status"] == "candidate_ready"
    assert result["row"]["code"] == "880001"
    assert result["row_count"] == 1
    assert result["production_write"] is False


@pytest.mark.parametrize(
    "payload, expected",
    [
        (reply(rows=[], total=0), "source_empty"),
        (reply(rows=[
            ["880001", "20261001", 1, 2, 1, 2, 10, 20],
            ["880001", "20261001", 1, 2, 1, 2, 10, 20],
        ], total=2), "invalid_source"),
        (reply(rows=[["880002", "20261001", 1, 2, 1, 2, 10, 20]]), "invalid_source"),
        (reply(rows=[["880001", "20261001", 1, 2, 1, 2, None, 20]]), "invalid_source"),
    ],
)
def test_probe_rejects_empty_mismatch_null_and_duplicate(payload, expected):
    result = validate_sector_daily_response(key(), payload)
    assert result["status"] == expected
    assert result["production_write"] is False


def test_stage_saves_raw_payload_and_candidate_receipt(tmp_path):
    keys = [key()]
    result = stage_sector_daily(keys, tmp_path, probe=lambda item: reply())
    assert result["counts"] == {"added": 1, "blocked": 0, "rejected": 0, "source_empty": 0}
    assert result["production_write"] is False
    assert (tmp_path / "raw" / "880001_20261001.json").exists()
    assert (tmp_path / "tdx_sector_daily" / "part_0000.parquet").exists()
    payload = json.loads((tmp_path / "receipt.json").read_text(encoding="utf-8"))
    assert payload["production_write"] is False
    assert payload["rows"][0]["source_response_json_sha256"]


def test_reconcile_definition_uses_validity_window_for_historical_rows():
    definitions = [{"code": "880001"}, {"code": "880002"}]
    daily = [
        {"code": "880001", "trade_date": "20200101"},
        {"code": "880002", "trade_date": "20200101"},
        {"code": "880002", "trade_date": "20261001"},
    ]
    validity = [
        {"code": "880001", "valid_from": "20250101", "valid_to": "20251231"},
        {"code": "880002", "valid_from": "20200101", "valid_to": None},
    ]
    result = reconcile_sector_definition(definitions, daily, validity)
    assert result["missing"] == []
    assert result["extra"] == [("880001", "20200101")]


def test_stage_by_date_uses_full_day_response_and_marks_missing_source_empty(tmp_path):
    payload = reply(rows=[["880001", "20261001", 1, 2, 1, 2, 10, 20]], total=1)
    result = stage_sector_daily_by_date(
        [key("880001"), key("880002")], tmp_path,
        fetch_day=lambda day: payload,
    )
    assert result["counts"] == {"added": 1, "blocked": 1, "rejected": 0, "source_empty": 1}
    assert result["production_write"] is False

