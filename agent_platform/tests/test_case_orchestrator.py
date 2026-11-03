# -*- coding: utf-8 -*-
"""服务端 Case orchestrator：5角色 → 会议 → 方案；审批/下发不自动做。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent_platform"))

from legion_command import case_api, case_orchestrator as co  # noqa: E402


def _fake(agent, system, user, timeout=180):
    return {"text": agent + " 意见", "binding": "glm/glm-5.3", "degraded": False}


def test_autorun_to_proposed(tmp_path, monkeypatch):
    monkeypatch.setattr(co, "_chat", _fake)
    c = case_api.save_case({"case_id": "CASE-AUTO-001", "title": "自动编排",
                            "materials": "材料", "status": "DRAFT", "opinions": [],
                            "meeting_id": "", "proposal": ""}, tmp_path / "cases")
    out = co.autorun("CASE-AUTO-001", case_root=tmp_path / "cases", meetings=tmp_path / "meetings")
    assert out["status"] == "PROPOSED" and out["proposal"]
    assert len([x for x in out["opinions"] if x.get("text")]) == 5
    assert out["meeting_id"]
    assert out["orchestration"]["state"] == "done"
    assert (tmp_path / "meetings" / (out["meeting_id"] + ".json")).is_file()


def test_autorun_failure_is_visible(tmp_path, monkeypatch):
    def flaky(agent, system, user, timeout=180):
        if agent == "AI1":
            raise RuntimeError("model down")
        return _fake(agent, system, user, timeout)
    monkeypatch.setattr(co, "_chat", flaky)
    case_api.save_case({"case_id": "CASE-AUTO-002", "title": "失败可见",
                        "materials": "材料", "status": "DRAFT", "opinions": [],
                        "meeting_id": "", "proposal": ""}, tmp_path / "cases")
    try:
        co.autorun("CASE-AUTO-002", case_root=tmp_path / "cases", meetings=tmp_path / "meetings")
        assert False
    except RuntimeError as e:
        assert "review_incomplete" in str(e)
    c = case_api.get_case("CASE-AUTO-002", tmp_path / "cases")
    assert c["orchestration"]["state"] == "error" and "review" in c["orchestration"]["phase"]
