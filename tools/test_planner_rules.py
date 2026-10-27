# -*- coding: utf-8 -*-
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agent_platform"))
from legion_command import planner_rules as P  # noqa: E402


def _setup(tmp_path, monkeypatch):
    exp = tmp_path / "experience"
    exp.mkdir()
    monkeypatch.setattr(P, "EXPERIENCE", exp)
    monkeypatch.setattr(P, "ACCEPTED", exp / "accepted.jsonl")
    monkeypatch.setattr(P, "LATEST", exp / "latest.json")
    monkeypatch.setattr(P, "RULES", tmp_path / "planner_rules.json")
    monkeypatch.setattr(P, "AB_DIR", tmp_path / "ab")


def test_no_signed_experience_cannot_build(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    res = P.build_rules()
    assert res["ok"] is False
    assert res["error"] == "no_signed_accepted_experience"


def test_signed_rules_activate_and_apply(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    rec = {"status": "accepted", "signed_by": "老田",
           "suggestions": [{"id": "S1"}, {"id": "S2"}]}
    P.ACCEPTED.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
    sr = P.signed_rules()
    assert sr["enabled"] and sr["signed_by"] == ["老田"]
    out = P.build_rules()
    assert out["ok"] and out["rules"].get("require_checks")
    act = P.active_rules()
    assert act["enabled"]
    plan = {"valid": True, "tasks": [
        {"id": "T1", "write_scope": ["docs/a.md"], "acceptance": ["x"]},
        {"id": "T2", "write_scope": ["docs/a.md"], "acceptance": ["y"]},
    ]}
    applied = P.apply_rules(plan, act)
    assert applied["planner_rules"]["checks_added"] >= 1
    assert applied["planner_rules"]["scope_conflicts"]


def test_candidate_is_simulation_only(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    cand = {"candidate_id": "c1", "suggestions": [{"id": "S1"}]}
    P.LATEST.write_text(json.dumps(cand), encoding="utf-8")
    cr = P.candidate_rules()
    assert cr["enabled"] is False and cr["rules"].get("require_checks")
