import json

import pandas as pd
import pytest

import history_backfill as hb


def test_validate_frame_rejects_duplicate_keys():
    frame = pd.DataFrame(
        [["600519.SH", "20260928"], ["600519.SH", "20260928"]],
        columns=["ts_code", "trade_date"],
    )
    with pytest.raises(ValueError, match="主键重复"):
        hb.validate_frame(frame, ["ts_code", "trade_date"], "20260928")


def test_validate_frame_rejects_date_scope():
    frame = pd.DataFrame([["600519.SH", "20260929"]], columns=["ts_code", "trade_date"])
    with pytest.raises(ValueError, match="日期越界"):
        hb.validate_frame(frame, ["ts_code", "trade_date"], "20260928")


def test_pull_one_reuses_complete_receipt(tmp_path, monkeypatch):
    stage = tmp_path / "stage"
    parquet = stage / "ts_daily_basic_20260928.parquet"
    receipt = stage / "ts_daily_basic_20260928.receipt.json"
    stage.mkdir()
    pd.DataFrame([["600519.SH", "20260928", 10.0]],
                 columns=["ts_code", "trade_date", "close"]).to_parquet(parquet, index=False)
    receipt.write_text(json.dumps({"complete": True, "staged_sha256": "x"}), encoding="utf-8")
    monkeypatch.setattr(hb, "_post", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("不应重复请求")))
    result = hb.pull_one("ts_daily_basic", "daily_basic", "20260928", stage)
    assert result["reused"] is True
