# -*- coding: utf-8 -*-
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agent_platform"))
from legion_command import mission_planner as P  # noqa: E402


def test_backend_py_task_is_manual(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "MISSIONS", tmp_path / "missions")
    plan = P.plan("KG 血缘", "修改 `/api/kg/lineage` 查询。", use_llm=False, apply_planner_rules=False)
    t2 = next(t for t in plan["tasks"] if t["id"] == "T2")
    assert any(s.endswith(".py") for s in t2["write_scope"])
    assert t2["worker_eligible"] is False
