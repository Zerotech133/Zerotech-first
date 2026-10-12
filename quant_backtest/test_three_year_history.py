import json
from pathlib import Path

import pandas as pd
import pytest

import three_year_history as tyh
import run_three_year_history as runner


def _calendar(root: Path, rows: list[tuple[str, str, int]]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=["exchange", "cal_date", "is_open"]).to_parquet(
        root / "part_0000.parquet", index=False
    )


def test_load_trade_dates_filters_sse_open_days_and_rejects_duplicates(tmp_path):
    root = tmp_path / "calendar"
    _calendar(root, [("SSE", "2023-01-03", 1), ("SSE", "2023-01-04", 0), ("SZSE", "2023-01-05", 1)])
    assert tyh.load_trade_dates(root, "20230101", "20230131") == ["20230103"]

    _calendar(root, [("SSE", "2023-01-03", 1), ("SSE", "20230103", 1)])
    with pytest.raises(ValueError, match="重复交易日"):
        tyh.load_trade_dates(root, "20230101", "20230131")


def test_load_trade_dates_ignores_identical_calendar_snapshots(tmp_path):
    root = tmp_path / "calendar"
    frame = pd.DataFrame(
        [("SSE", "2023-01-03", 1), ("SSE", "2023-01-04", 1)],
        columns=["exchange", "cal_date", "is_open"],
    )
    root.mkdir(parents=True)
    frame.to_parquet(root / "part_0000.parquet", index=False)
    frame.to_parquet(root / "part_0001.parquet", index=False)
    assert tyh.load_trade_dates(root, "20230101", "20230131") == ["20230103", "20230104"]


def test_build_batches_keeps_20_to_50_dates_per_batch():
    dates = [f"2023{day:04d}" for day in range(1, 96)]
    batches = tyh.build_batches(dates, batch_size=40)
    assert [len(item["trade_dates"]) for item in batches] == [40, 40, 15]
    assert batches[0]["batch_id"] == "batch_001"


def test_audit_batches_requires_complete_receipt_and_raw_payload(tmp_path):
    stage = tmp_path / "stage"
    batch = {"batch_id": "batch_001", "trade_dates": ["20230103"]}
    table_dir = stage / "batch_001"
    table_dir.mkdir(parents=True)
    receipt = {
        "complete": True,
        "status": "candidate_ready",
        "request_params": {"trade_date": "20230103"},
        "raw_path": str(table_dir / "ts_daily_basic_20230103.json"),
        "staged_path": str(table_dir / "ts_daily_basic_20230103.parquet"),
    }
    (table_dir / "ts_daily_basic_20230103.receipt.json").write_text(
        json.dumps(receipt), encoding="utf-8"
    )
    (table_dir / "ts_daily_basic_20230103.json").write_text("{}", encoding="utf-8")
    (table_dir / "ts_daily_basic_20230103.parquet").write_bytes(b"x")
    report = tyh.audit_batches(stage, [batch], ["ts_daily_basic"])
    assert report["complete_count"] == 1
    assert report["missing"] == []

    (table_dir / "ts_daily_basic_20230103.json").unlink()
    report = tyh.audit_batches(stage, [batch], ["ts_daily_basic"])
    assert report["complete_count"] == 0
    assert report["missing"] == ["batch_001:ts_daily_basic:20230103"]


def test_run_three_year_reuses_complete_units_and_writes_raw_payload(tmp_path, monkeypatch):
    calls = []

    def fake_pull(table, api, day, stage_dir, primary_key):
        calls.append((table, api, day))
        stage_dir = Path(stage_dir)
        stage_dir.mkdir(parents=True, exist_ok=True)
        raw = stage_dir / f"{table}_{day}.json"
        raw.write_text(json.dumps({"code": 0, "data": {"items": [], "fields": []}}), encoding="utf-8")
        parquet = stage_dir / f"{table}_{day}.parquet"
        parquet.write_bytes(b"parquet-placeholder")
        return {"table": table, "api_name": api, "request_params": {"trade_date": day}, "complete": True,
                "status": "candidate_ready", "raw_path": str(raw), "staged_path": str(parquet), "row_count": 0}

    monkeypatch.setattr(tyh, "pull_one_with_raw", fake_pull)
    result = tyh.run_three_year(
        [tyh.TableSpec("ts_daily_basic", "daily_basic", ("ts_code", "trade_date"))],
        ["20230103"], tmp_path / "stage", batch_size=20,
    )
    assert result["production_write"] is False
    assert calls == [("ts_daily_basic", "daily_basic", "20230103")]

    result = tyh.run_three_year(
        [tyh.TableSpec("ts_daily_basic", "daily_basic", ("ts_code", "trade_date"))],
        ["20230103"], tmp_path / "stage", batch_size=20,
    )
    assert result["reused_count"] == 1
    assert len(calls) == 1


def test_make_plan_defaults_to_read_only_and_dry_run(tmp_path):
    calendar = tmp_path / "calendar"
    _calendar(calendar, [("SSE", "2023-01-03", 1), ("SSE", "2023-01-04", 1)])
    plan = runner.make_plan(calendar, "20230101", "20230131", 20, ["ts_daily_basic"])
    assert plan["trade_date_count"] == 2
    assert plan["batch_count"] == 1
    assert plan["production_write"] is False
    assert plan["global_activation"] is False


def test_cli_writes_audit_report_for_dry_run(tmp_path, monkeypatch):
    calendar = tmp_path / "calendar"
    _calendar(calendar, [("SSE", "2023-01-03", 1), ("SSE", "2023-01-04", 1)])
    work = tmp_path / "work"
    monkeypatch.setattr(runner, "DEFAULT_WORK", work)

    assert runner.main([
        "--calendar-root", str(calendar),
        "--stage-root", str(tmp_path / "stage"),
        "--start", "20230101", "--end", "20230131",
        "--batch-size", "20", "--table", "ts_daily", "--dry-run",
    ]) == 0

    audit = json.loads((work / "three_year_history_audit.json").read_text(encoding="utf-8"))
    assert audit["total_count"] == 2
    assert audit["complete_count"] == 0
    assert audit["production_write"] is False
