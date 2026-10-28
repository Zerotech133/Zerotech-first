# -*- coding: utf-8 -*-
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agent_platform"))
from legion_command import mission_planner as P  # noqa: E402
from legion_command import planner_rules as R  # noqa: E402


def test_keyword_maps_backend_file(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "MISSIONS", tmp_path / "missions")
    plan = P.plan("MCP 工具桥", "实现 MCP 工具桥，支持 tools/list 与 tools/call。",
                  use_llm=False, apply_planner_rules=False)
    t2 = next(t for t in plan["tasks"] if t["id"] == "T2")
    assert "agent_platform/legion_command/mcp_bridge.py" in t2["write_scope"]


def test_multi_file_checks():
    plan = {"tasks": [{"id": "T1", "write_scope": ["a.py", "b.py", "dir/"]}]}
    out = R.apply_rules(plan, {"rules": {"require_checks": True}})
    checks = out["tasks"][0]["checks"]
    assert [c["path"] for c in checks] == ["a.py", "b.py"]
    assert out["planner_rules"]["checks_added"] == 2
