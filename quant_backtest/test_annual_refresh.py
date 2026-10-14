# -*- coding: utf-8 -*-
"""年度历史回补的分批与失败恢复。"""
from __future__ import annotations

from datetime import date, timedelta
import json

from run_annual_history_backfill import resume_annual_refresh
from run_explicit_history_backfill import build_explicit_retry_units


def _days(n=70):
    start = date(2023, 1, 3)
    return [(start + timedelta(days=i)).strftime("%Y%m%d") for i in range(n)]


def test_annual_refresh_retries_only_failed_batches(tmp_path):
    calls = []

    def fetch(unit):
        calls.append(unit["unit_id"])
        if unit["unit_id"].endswith("0002"):
            return {"status": "source_empty", "rows": []}
        return {"status": "candidate_ready", "rows": [{"ts_code": "000001.SZ", "trade_date": unit["scope"]["trading_days"][0]}]}

    first = resume_annual_refresh("ts_daily_basic", 2023, _days(), tmp_path, fetch=fetch, batch_days=30)
    assert first["status"] == "blocked"
    assert first["failed_units"] == 1
    assert len(calls) == 3
    assert json.loads((tmp_path / "annual_2023_manifest.json").read_text(encoding="utf-8"))["entries"][2]["reason"] is None

    calls.clear()
    second = resume_annual_refresh("ts_daily_basic", 2023, _days(), tmp_path, fetch=fetch, batch_days=30)
    assert second["status"] == "blocked"
    assert second["retried_units"] == 1
    assert calls == ["ts_daily_basic:20230202-20230303:0002"]
    assert json.loads((tmp_path / "annual_2023_manifest.json").read_text(encoding="utf-8"))["production_write"] is False


def test_annual_refresh_rejects_duplicate_rows_without_publishing(tmp_path):
    def fetch(unit):
        day = unit["scope"]["trading_days"][0]
        return {"status": "candidate_ready", "rows": [
            {"ts_code": "000001.SZ", "trade_date": day},
            {"ts_code": "000001.SZ", "trade_date": day},
        ]}

    result = resume_annual_refresh("ts_margin_detail", 2024, _days(20), tmp_path, fetch=fetch, batch_days=20)
    assert result["status"] == "blocked"
    assert result["reason"] == "duplicate_key"
    assert result["publication"] == "not_started"


def test_explicit_retry_units_skip_complete_receipts():
    receipts = [
        {"table": "ts_daily_basic", "request_params": {"trade_date": "20260928"}, "complete": True, "status": "candidate_ready"},
        {"table": "ts_margin_detail", "request_params": {"trade_date": "20261008"}, "complete": False, "status": "source_empty"},
    ]
    retry = build_explicit_retry_units(receipts)
    assert retry == [{"table": "ts_margin_detail", "trade_date": "20261008"}]
