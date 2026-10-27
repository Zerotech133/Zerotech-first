# -*- coding: utf-8 -*-
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agent_platform"))
from legion_command import mission_dispatch as D  # noqa: E402
from legion_command import mission_planner as P  # noqa: E402


def test_fallback_plan_and_validate(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "MISSIONS", tmp_path)
    res = P.plan("演示任务", "# 目标\n做一个页面", use_llm=False)
    assert res["valid"] is True and len(res["tasks"]) == 4
    assert (tmp_path / res["mission_id"] / "plan.json").exists()


def test_validate_cycle_and_bad_owner():
    plan = {"tasks": [
        {"id": "T1", "title": "a", "owner": "ARCH1", "deps": ["T2"], "write_scope": ["a/"], "acceptance": ["x"]},
        {"id": "T2", "title": "b", "owner": "NOPE", "deps": ["T1"], "write_scope": ["b/"], "acceptance": ["y"]},
    ]}
    errs = P.validate_plan(plan)
    assert any(x.startswith("bad_owner") for x in errs)
    assert any(x.startswith("cycle") for x in errs)


def test_dry_run_order_waves_conflict(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "MISSIONS", tmp_path)
    plan = {
        "schema": "mission_plan/v1", "mission_id": "M-TEST", "valid": True,
        "tasks": [
            {"id": "T1", "title": "架构", "owner": "ARCH1", "deps": [],
             "write_scope": ["agent_platform/"], "acceptance": ["a"]},
            {"id": "T2", "title": "后端", "owner": "DEV1", "deps": ["T1"],
             "write_scope": ["agent_platform/api/"], "acceptance": ["b"]},
            {"id": "T3", "title": "前端", "owner": "DEV5", "deps": ["T1"],
             "write_scope": ["portal/"], "acceptance": ["c"]},
        ],
    }
    res = D.dry_run(plan, parallel_limit=2)
    assert res["ok"] is True
    assert res["order"][0] == "T1"
    assert res["waves"][0] == ["T1"]
    assert res["rows"][0]["ticket_id"].startswith("K-MISSION-M-TEST-T1")
    # agent_platform/ 与 agent_platform/api/ 视为冲突（需串行）
    assert any(c["a"] == "T1" and c["b"] == "T2" for c in res["conflicts"])
