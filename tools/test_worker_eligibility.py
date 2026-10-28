# -*- coding: utf-8 -*-
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agent_platform"))
from legion_command import mission_planner as P  # noqa: E402
from legion_command import mission_worker as W  # noqa: E402


def test_worker_eligibility_and_dryrun(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "MISSIONS", tmp_path / "missions")
    monkeypatch.setattr(W, "REPO", tmp_path)
    monkeypatch.setattr(W, "MISSIONS", tmp_path / "missions")
    doc = "在 `/keys` 页面增加筛选；不改后端接口。"
    plan = P.plan("Worker兼容验证", doc, use_llm=False, apply_planner_rules=False)
    tasks = {t["id"]: t for t in plan["tasks"]}
    assert tasks["T2"]["worker_eligible"] is False
    assert tasks["T1"]["worker_eligible"] is True
    assert tasks["T3"]["worker_eligible"] is True
    mdir = tmp_path / "missions" / plan["mission_id"]
    (mdir / "state.json").write_text(json.dumps({"mission_id": plan["mission_id"], "tasks": {
        tid: {"status": "queued", "ticket_id": "K-TEST-DRYRUN"} for tid in tasks
    }}), encoding="utf-8")
    (tmp_path / ".worktrees" / "K-TEST-DRYRUN").mkdir(parents=True)
    t2 = W.run_task(plan["mission_id"], "T2", dry_run=True)
    assert t2["ok"] is False and t2["error"] == "worker_not_eligible"
    t3 = W.run_task(plan["mission_id"], "T3", dry_run=True)
    assert t3["ok"] is True and t3["mode"] == "dry-run"
