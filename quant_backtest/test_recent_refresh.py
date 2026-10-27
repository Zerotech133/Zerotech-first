# -*- coding: utf-8 -*-
"""最近 30 个交易日刷新：来源资格、staging 和回读对账。"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pandas as pd

from full_refresh_plan import execute_recent_refresh
from valuation_field_reconcile import read_only_valuation_check
from data_health import scan_recent_refresh_receipts


def _gaps():
    return [
        {"table": "stock_basic", "code": "000001.SZ", "trade_date": "20261009"},
        {"table": "tdx_sector_daily", "code": "BK0001", "trade_date": "20261009"},
        {"table": "tdx_sector_min", "code": "BK0002", "trade_date": "20261009"},
        {"table": "ts_margin_detail", "code": "000001.SZ", "trade_date": "20261009"},
    ]


def test_recent_refresh_blocks_without_qualified_source_and_writes_receipts(tmp_path):
    result = execute_recent_refresh(
        _gaps(),
        tmp_path,
        source_probe=lambda key: {
            "status": "source_empty",
            "row_count": 0,
            "reason": "remote_no_supply",
        },
        trading_days=[f"202609{i:02d}" for i in range(1, 31)],
    )

    assert result["status"] == "blocked"
    assert result["production_write"] is False
    assert result["global_activation"] is False
    assert result["qualified"] == 0
    assert result["blocked"] == len(_gaps())
    assert (tmp_path / "source_receipt.jsonl").exists()
    assert (tmp_path / "commit_intent.jsonl").exists()
    assert (tmp_path / "commit_complete.jsonl").exists()
    assert (tmp_path / "verification.json").exists()
    receipts = [json.loads(line) for line in (tmp_path / "source_receipt.jsonl").read_text(encoding="utf-8").splitlines()]
    assert all(item["status"] == "blocked" for item in receipts)


def test_recent_refresh_rejects_duplicate_fact_keys_and_keeps_staging(tmp_path):
    rows = [
        {"ts_code": "000001.SZ", "trade_date": "20261009", "close": 10.0},
        {"ts_code": "000001.SZ", "trade_date": "20261009", "close": 10.1},
    ]

    def probe(_key):
        return {"status": "qualified", "row_count": 1, "rows": rows}

    result = execute_recent_refresh(
        [{"table": "ts_daily_basic", "code": "000001.SZ", "trade_date": "20261009"}],
        tmp_path,
        source_probe=probe,
        trading_days=[f"202609{i:02d}" for i in range(1, 31)],
    )

    assert result["status"] == "blocked"
    assert result["rejected"] == 1
    assert result["reason"] == "duplicate_key"
    assert list(tmp_path.glob("staging/*.jsonl"))
    assert result["publication"] == "not_started"


def test_recent_refresh_uses_twenty_to_thirty_day_windows(tmp_path):
    days = [(date(2026, 9, 1) + timedelta(days=i)).strftime("%Y%m%d") for i in range(60)]
    result = execute_recent_refresh(
        [], tmp_path, source_probe=lambda _key: {"status": "qualified", "row_count": 0},
        trading_days=days, batch_days=30,
    )
    assert result["windows"] == [30, 30]


def test_valuation_check_reports_view_coverage_without_mutating_input(tmp_path):
    kline = pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20261009", "close": 10.0}])
    source = tmp_path / "ts_daily_basic"
    source.mkdir()
    # 没有来源分片时必须明确返回 source_empty，而不是把空值当成已补齐。
    result = read_only_valuation_check(kline, source)
    assert result["status"] == "source_empty"
    assert result["coverage"]["fields"]["market_cap"]["missing"] == 1
    assert "market_cap" not in kline.columns


def test_health_scan_reads_recent_receipt_status_and_volume(tmp_path):
    execute_recent_refresh(
        _gaps(), tmp_path,
        source_probe=lambda _key: {"status": "source_empty", "row_count": 0},
        trading_days=[f"202609{i:02d}" for i in range(1, 31)],
    )
    health = scan_recent_refresh_receipts(tmp_path)
    assert health["status"] == "blocked"
    assert health["blocked"] == 4
    assert health["production_write"] is False

