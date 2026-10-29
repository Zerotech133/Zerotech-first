# -*- coding: utf-8 -*-
"""dev_orchestrator MVP 测试（WO-HARNESS-O12）。"""
from __future__ import annotations

import importlib.util
import time
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "dev_orchestrator", str(_WT / "tools" / "dev_orchestrator.py"))
O = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(O)

NOW = time.mktime(time.strptime("2026-10-27 17:00:00", "%Y-%m-%d %H:%M:%S"))


def _tasks():
    return {
        "T-DONE": {"status": "done", "owner": "DEV1",
                   "created": "2026-10-26 10:00:00",
                   "merged": "2026-10-26 11:00:00"},
        "T-READY": {"status": "doing", "owner": "DEV5",
                    "created": "2026-10-27 10:00:00"},
        "T-BLOCKED": {"status": "doing", "owner": "DEV6",
                      "created": "2026-10-27 09:00:00",
                      "deps": ["T-PENDING"]},
        "T-PENDING": {"status": "doing", "owner": "DEV7",
                      "created": "2026-10-25 09:00:00"},
        "T-REVIEW": {"status": "review", "owner": "DEV8",
                     "created": "2026-10-27 16:00:00"},
    }


def test_compute_ready_blocked_capacity_stale():
    rep = O.compute_status(_tasks(), {}, now=NOW, max_concurrent=2,
                           stale_hours=24)
    assert rep["capacity"]["in_flight"] == 4
    assert rep["capacity"]["over_capacity"] is True
    ready = {r["id"] for r in rep["ready"]}
    assert "T-READY" in ready and "T-REVIEW" in ready
    assert "T-BLOCKED" not in ready
    blocked = {b["id"]: b["deps"] for b in rep["blocked"]}
    assert blocked.get("T-BLOCKED") == ["T-PENDING"]
    stale = {s["id"] for s in rep["stale"]}
    assert "T-PENDING" in stale          # created 10-25，>24h
    assert "T-BLOCKED" not in stale      # created 当日 09:00，<24h
    snap = rep["snapshot"]
    assert snap["tasks_context"]["recently_completed"] == ["T-DONE"]
    assert set(snap["next_steps"]) >= {"T-READY"}


def test_state_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    st = O.load_state(path)
    assert st["version"] == 1 and st["goals"]
    st["decisions_made"].append("d1")
    st["goals"] = ["goal-x"]
    O.save_state(st, path)
    st2 = O.load_state(path)
    assert st2["decisions_made"] == ["d1"]
    assert st2["goals"] == ["goal-x"]


def test_deps_string_and_list():
    assert O._deps({"deps": "A,B"}) == ["A", "B"]
    assert O._deps({"deps": ["A", " ", "B"]}) == ["A", "B"]


def test_missing_tasks():
    assert O.load_tasks(Path("nope-does-not-exist.json")) == {}
