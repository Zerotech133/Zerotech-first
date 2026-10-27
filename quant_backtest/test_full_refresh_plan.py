from full_refresh_plan import build_full_refresh_plan, execute_refresh_batch
from datetime import date, timedelta


def test_full_refresh_plan_splits_trading_days_without_overlap():
    days = [
        (date(2026, 1, 1) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(41)
    ]
    plan = build_full_refresh_plan(
        "ts_daily",
        "20260101",
        "20260228",
        batch_days=20,
        trading_days=days,
        source="tushare",
        market="cn",
    )
    windows = [unit["scope"]["trading_days"] for unit in plan["units"]]
    assert [len(window) for window in windows] == [20, 20, 1]
    assert [day for window in windows for day in window] == [
        day.replace("-", "") for day in days
    ]
    assert len({unit["unit_id"] for unit in plan["units"]}) == len(plan["units"])
    assert plan["plan_digest"]
    assert plan["production_write"] is False


def test_execute_refresh_batch_dry_run_does_not_create_run(tmp_path):
    days = [f"2026-01-{i:02d}" for i in range(1, 21)]
    plan = build_full_refresh_plan(
        "ts_daily", "20260101", "20260131", trading_days=days, source="tdx", market="cn"
    )
    result = execute_refresh_batch(plan, str(tmp_path / "runs.sqlite"), dry_run=True)
    assert result["dry_run"] is True
    assert result["production_write"] is False
    assert result["run_id"] is None
