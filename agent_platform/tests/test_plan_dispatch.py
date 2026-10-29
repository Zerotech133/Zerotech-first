# -*- coding: utf-8 -*-
"""plan_dispatch 测试。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import plan_dispatch as P  # noqa: E402


def _root(tmp_path):
    org = {"departments": [{"dept": "dev", "agent_profiles": [
        {"agent_id": "DEV1", "discipline": "backend", "role_title": "后端"},
        {"agent_id": "DEV5", "discipline": "frontend", "role_title": "前端"},
    ]}]}
    (tmp_path / "agent_platform/registry").mkdir(parents=True)
    (tmp_path / "agent_platform/registry/org.json").write_text(
        json.dumps(org, ensure_ascii=False), encoding="utf-8")
    (tmp_path / ".worktrees").mkdir()
    (tmp_path / ".worktrees/tasks.json").write_text("{}", encoding="utf-8")
    return tmp_path


def _plan():
    return {"schema": P.SCHEMA, "project": "war-screen-v2", "elements": [
        {"id": "E1", "title": "API", "owner": "DEV1", "discipline": "backend",
         "acceptance": "pytest -q", "effort": "S", "deps": []},
        {"id": "E2", "title": "UI", "owner": "DEV5", "discipline": "frontend",
         "acceptance": "pytest -q", "effort": "S", "deps": ["E1"]},
    ]}


def test_valid_plan(tmp_path):
    errors, warnings = P.validate(_plan(), _root(tmp_path))
    assert not errors
    cmds = P.commands(_plan(), tmp_path)
    assert len(cmds) == 2
    assert any("devtask.py" in " ".join(c["devtask"]) for c in cmds)
    assert any("tianfeng_dispatch" in " ".join(c["dispatch"]) for c in cmds)


def test_bad_owner_and_cycle(tmp_path):
    plan = _plan()
    plan["elements"][0]["owner"] = "NOBODY"
    plan["elements"][1]["deps"] = ["E2"]
    errors, _ = P.validate(plan, _root(tmp_path))
    assert any("owner" in e for e in errors)
    assert any("环" in e for e in errors)


def test_missing_acceptance(tmp_path):
    plan = _plan()
    plan["elements"][0]["acceptance"] = ""
    errors, _ = P.validate(plan, _root(tmp_path))
    assert any("acceptance" in e for e in errors)
