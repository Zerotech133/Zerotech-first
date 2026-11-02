# -*- coding: utf-8 -*-
"""HQ-05 heartbeat 合并：纯去重 + 真实调度只读。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import hq_heartbeat as hh  # noqa: E402


def test_coalesce_same_agent():
    wakes = [
        {"agent": "A0A", "due": "09:00", "reason": "schedule", "source": "s1"},
        {"agent": "A0A", "due": "08:00", "reason": "assignment", "source": "T1"},
        {"agent": "D1", "due": "10:00", "reason": "schedule", "source": "s2"},
    ]
    out = hh.coalesce_wakes(wakes)
    assert len(out) == 2
    a0 = next(x for x in out if x["agent"] == "A0A")
    assert a0["count"] == 2
    assert a0["due"] == "08:00"
    assert set(a0["reasons"]) == {"schedule", "assignment"}


def test_real_schedules_shape():
    h = hh.build_heartbeat()
    assert h["read_only"] is True
    assert h["schedules"]["total"] >= 1
    assert h["summary"]["agents_to_wake"] <= h["summary"]["raw_wakes"]
    assert isinstance(h["schedules"]["multi_schedule_agents"], list)


def test_active_tasks_mapped():
    h = hh.build_heartbeat()
    for a in h["active_tasks"]:
        assert "task_id" in a and "status" in a


def test_json_cli_pure(capsys):
    rc = hh.main(["--json"])
    assert rc == 0
    d = json.loads(capsys.readouterr().out)
    assert d["schedules"]["total"] >= 1
    assert d["summary"]["agents_to_wake"] >= 1
