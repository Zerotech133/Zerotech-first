# -*- coding: utf-8 -*-
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agent_platform"))
from legion_command import mission_planner as P  # noqa: E402


def test_api_and_dataops_routes(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "MISSIONS", tmp_path / "missions")
    api = P.plan("凭据 API", "给 `/api/credentials` 增加参数校验。", use_llm=False, apply_planner_rules=False)
    t2 = next(t for t in api["tasks"] if t["id"] == "T2")
    assert "agent_platform/legion_command/serve.py" in t2["write_scope"]
    assert "agent_platform/legion_command/credential_catalog.py" in t2["write_scope"]

    dataops = P.plan("数据运营页", "修改 `/dataops` 页面说明。", use_llm=False, apply_planner_rules=False)
    t3 = next(t for t in dataops["tasks"] if t["id"] == "T3")
    assert "portal/dataops.html" in t3["write_scope"]
