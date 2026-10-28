# -*- coding: utf-8 -*-
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agent_platform"))
from legion_command import mission_planner as P  # noqa: E402
from legion_command import planner_rules as R  # noqa: E402


def test_fallback_exact_files_and_rule_checks(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "MISSIONS", tmp_path / "missions")
    doc = "在 `/keys` 页面增加类别筛选；不改后端接口。"
    base = P.plan("统一凭据类别筛选", doc, use_llm=False, apply_planner_rules=False)
    t1 = next(t for t in base["tasks"] if t["id"] == "T1")
    t3 = next(t for t in base["tasks"] if t["id"] == "T3")
    assert t1["write_scope"][0].startswith("deliverables/specs/")
    assert t1["write_scope"][0].endswith(".md")
    assert t3["write_scope"] == ["agent_platform/legion_command/static/keypool_v2.html"]
    cand = R.apply_rules(base, {"rules": {"require_checks": True}})
    checks = sum(len(t.get("checks") or []) for t in cand["tasks"])
    assert checks >= 2
    assert cand["planner_rules"]["checks_added"] >= 2
