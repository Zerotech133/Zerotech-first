# -*- coding: utf-8 -*-
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agent_platform"))
from legion_command import mission_planner as P  # noqa: E402


def test_paper_route_and_keyword(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "MISSIONS", tmp_path / "missions")
    paper = P.plan("Paper 路由", "在 `/paper` 页面增加说明。", use_llm=False, apply_planner_rules=False)
    t3 = next(t for t in paper["tasks"] if t["id"] == "T3")
    assert t3["write_scope"] == ["portal/paper.html"] and t3["worker_eligible"] is True

    keys = P.plan("密钥轮换", "新增密钥轮换说明。", use_llm=False, apply_planner_rules=False)
    t2 = next(t for t in keys["tasks"] if t["id"] == "T2")
    assert "agent_platform/legion_command/keypool.py" in t2["write_scope"]
