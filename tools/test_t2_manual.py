# -*- coding: utf-8 -*-
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agent_platform"))
from legion_command import mission_dispatch as D  # noqa: E402
from legion_command import mission_planner as P  # noqa: E402


def test_manual_task_waits_human(monkeypatch):
    monkeypatch.setattr(D, "_create_ticket", lambda plan, task: {
        "ok": True, "ticket_id": "K-TEST-" + task["id"], "detail": ""})
    state = {"tasks": {
        "T1": {"title": "backend", "owner": "DEV1", "deps": [], "worker_eligible": False,
               "status": "blocked"},
        "T2": {"title": "docs", "owner": "ARCH1", "deps": [], "worker_eligible": True,
               "status": "blocked"},
    }}
    plan = {"mission_id": "M-TEST", "tasks": []}
    D._schedule(state, plan, 3)
    assert state["tasks"]["T1"]["status"] == "waiting_human"
    assert state["tasks"]["T1"]["manual_owner"] == "DEV1"
    assert state["tasks"]["T2"]["status"] == "doing"


def test_fine_grained_routes(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "MISSIONS", tmp_path / "missions")
    kg = P.plan("KG 血缘", "修改 `/api/kg/lineage` 查询。", use_llm=False, apply_planner_rules=False)
    t2 = next(t for t in kg["tasks"] if t["id"] == "T2")
    assert "portal/api_lineage.py" in t2["write_scope"]
    ops = P.plan("DataOps", "修改 `/api/dataops/overview`。", use_llm=False, apply_planner_rules=False)
    t2 = next(t for t in ops["tasks"] if t["id"] == "T2")
    assert "portal/api_dataops.py" in t2["write_scope"]
