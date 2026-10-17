# -*- coding: utf-8 -*-
from datetime import date, datetime, timezone
import json
import sys
from pathlib import Path

PORTAL_ROOT = Path(__file__).resolve().parents[1]
QBT_ROOT = PORTAL_ROOT.parent / "quant_backtest"
sys.path.insert(0, str(PORTAL_ROOT))
sys.path.insert(0, str(QBT_ROOT))

import api_backtest as api
from bar_dto import Bar
from bar_store import BarStore
from execution_engine import OrderIntent


def _bar(minute):
    dt = datetime(2026, 10, 16, 1, minute, tzinfo=timezone.utc)
    return Bar("000001.SZ", "CN", "1m", dt, date(2026, 10, 16), 10, 11, 9, 10, 100, 100, dt, dt, True, "fixture", "v1", "none", "observed", ("fixture",))


def test_minute_start_worker_status_result(tmp_path, monkeypatch):
    store = BarStore([_bar(30), _bar(31)])
    api.register_minute_source(
        "fixture", store,
        [OrderIntent("000001.SZ", "BUY", 100, _bar(30).dt)],
        status_provider=lambda *args: "TRADING",
        limit_provider=lambda *args: {"known": True},
    )
    monkeypatch.setattr(api, "_BT2_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(api, "_BT2_DIR", str(tmp_path / "daily"))
    monkeypatch.setattr(api, "STATION", str(tmp_path))
    params = {"execution_mode": "minute_store", "source_ref": "fixture", "strategy": "minute_store", "start_at": "2026-10-16T01:29:00+00:00", "end_at": "2026-10-16T02:00:00+00:00", "as_of": "2026-10-16T02:00:00+00:00"}
    import backtest_engine_v2 as eng
    monkeypatch.setattr(eng, "MINUTE_OUT_DIR", str(tmp_path / "minute"))
    rec = api._bt2_start(params)
    assert rec["ok"]
    jid = rec["job"]["id"]
    api._bt2_worker(jid, {**rec["job"]["params"], "initial_cash": 2000})
    result = api._bt2_result(jid)
    if not result.get("ok"):
        state = api._read_json(api._bt2_state_path(jid))
        raise AssertionError(f"minute worker failed: {state}")
    assert result["schema"] == "minute-exec-v1"
    assert "metrics" in result and "orders" in result and "fills" in result and "audits" in result


def test_minute_start_rejects_unregistered_source():
    result = api._bt2_start({"execution_mode": "minute_store", "source_ref": "unknown", "d0": "20261016", "d1": "20261017"})
    assert result["ok"] is False
    assert "source_ref" in result["error"]


def test_jobs_project_source_descriptor_and_availability(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_BT2_STATE_DIR", str(tmp_path / "state"))
    api._bt2_write_state(
        "job-status",
        status="dead",
        params={"execution_mode": "minute_store", "source_ref": "missing"},
        error="SOURCE_UNAVAILABLE",
    )
    jobs = api._bt2_status()
    assert jobs["jobs"][0]["source_availability"] == "source_unavailable"
    assert jobs["jobs"][0]["source_error"] == "SOURCE_UNAVAILABLE"


def test_start_rejects_marked_unavailable_source(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_JOB_SPEC_CATALOG", str(tmp_path / "catalog.json"))
    api.register_minute_source("unavailable", BarStore([_bar(30)]),
                               [OrderIntent("000001.SZ", "BUY", 100, _bar(30).dt)],
                               status_provider=lambda *args: "TRADING",
                               limit_provider=lambda *args: {"known": True})
    api._JOB_SPEC_REGISTRY.mark_unavailable("unavailable", "SOURCE_UNAVAILABLE")
    result = api._bt2_start({"execution_mode": "minute_store", "source_ref": "unavailable", "d0": "20261016", "d1": "20261017"})
    assert result["ok"] is False
    assert result["error"] == "SOURCE_UNAVAILABLE"
    assert result["error_code"] == "SOURCE_UNAVAILABLE"
    assert result["source_ref"] == "unavailable"
    assert result["source_availability"] == "source_unavailable"
