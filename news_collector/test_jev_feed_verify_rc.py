# -*- coding: utf-8 -*-
"""JevFeed-Verify 回归：267009 是运行中，不能判 ❌。"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import jev_feed_verify as jv


def test_running_rc_is_treated_as_running():
    assert jv._is_running({"state": "Ready", "last_result": 267009}) is True
    assert jv._is_running({"state": "Running", "last_result": 0}) is True
    assert jv._is_running({"state": "Ready", "last_result": 0}) is False


def test_build_running_rc_is_warning_not_failure(monkeypatch):
    now = datetime(2026, 11, 5, 22, 30)
    ti = {
        "last_run": datetime(2026, 11, 4, 21, 0),
        "last_result": 267009,
        "state": "Ready",
        "raw": "2026/11/4 21:00:01",
    }
    detail = '{"wm": {"news": "20261104", "notice": "20261104"}}'
    old = '{"wm": {"news": "20261103", "notice": "20261103"}}'
    monkeypatch.setattr(jv, "task_info", lambda task: dict(ti))
    monkeypatch.setattr(jv, "wait_finish", lambda task: "Ready")
    monkeypatch.setattr(
        jv, "task_log_exit", lambda: (0, "2026-11-04T23:22:17", "")
    )
    monkeypatch.setattr(
        jv,
        "runs_latest",
        lambda: (
            ("2026-11-04T23:22:17", 1, 689, 0, detail),
            ("2026-11-03T21:00:21", 1, 500, 0, old),
        ),
    )
    monkeypatch.setattr(
        jv,
        "xd_stats",
        lambda: {"xd_news": (1667, "2026-11-04"), "xd_notice": (4230, "2026-11-04")},
    )

    icon, msg, why = jv.build(now)

    assert icon == "⚠️"
    assert "仍在运行" in msg
    assert "任务rc=267009" in msg
    assert why == "still_running"
