import json

import pandas as pd
import pytest

from sector_minute_gap_backfill import (
    probe_sector_minute,
    stage_sector_minute,
    validate_sector_minute_frame,
)


def frame():
    return pd.DataFrame([
        {"code": "880001", "trade_date": "20261001", "trade_time": "09:31",
         "open": 1., "high": 2., "low": 1., "close": 2., "vol": 10., "amount": 20.},
        {"code": "880001", "trade_date": "20261001", "trade_time": "09:32",
         "open": 2., "high": 3., "low": 2., "close": 3., "vol": 11., "amount": 21.},
    ])


def test_validate_minute_frame_accepts_valid_rows():
    result = validate_sector_minute_frame(frame())
    assert result["ok"] is True
    assert result["row_count"] == 2
    assert result["duplicate_keys"] == 0
    assert result["required_nulls"] == {"open": 0, "high": 0, "low": 0, "close": 0, "vol": 0, "amount": 0}


def test_validate_minute_frame_counts_required_nulls_and_duplicate_keys():
    bad = pd.concat([frame(), frame().iloc[[0]]], ignore_index=True)
    bad.loc[1, "vol"] = None
    result = validate_sector_minute_frame(bad)
    assert result["ok"] is False
    assert result["required_nulls"]["vol"] == 1
    assert result["duplicate_keys"] == 2


@pytest.mark.parametrize("trade_date, trade_time", [("20261301", "09:31"), ("20261001", "25:00")])
def test_validate_minute_frame_rejects_date_or_time_boundary(trade_date, trade_time):
    bad = frame()
    bad.loc[0, "trade_date"] = trade_date
    bad.loc[0, "trade_time"] = trade_time
    result = validate_sector_minute_frame(bad)
    assert result["ok"] is False
    assert result["invalid_keys"]


def test_stage_minute_saves_raw_payload_and_receipt(tmp_path):
    keys = [{"code": "880001", "trade_date": "20261001", "start_time": "09:31", "end_time": "15:00"}]
    payload = {"columns": list(frame().columns), "rows": frame().values.tolist(), "total": 2}
    result = stage_sector_minute(keys, tmp_path, probe=lambda item: payload)
    assert result["counts"] == {"added": 2, "blocked": 0, "rejected": 0, "source_empty": 0}
    assert result["production_write"] is False
    assert (tmp_path / "raw" / "880001_20261001.json").exists()
    assert (tmp_path / "tdx_sector_min" / "part_0000.parquet").exists()
    receipt = json.loads((tmp_path / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["production_write"] is False


def test_probe_rejects_rows_outside_requested_surface():
    payload = {"columns": list(frame().columns), "rows": frame().values.tolist(), "total": 2}
    result = probe_sector_minute("880002", "20261001", fetch=lambda params: payload)
    assert result["status"] == "blocked"
    assert result["reason"] == "remote_filter_ignored"

