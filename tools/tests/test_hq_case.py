# -*- coding: utf-8 -*-
"""Case 流程：田丰汇总→五角色→会议→方案→人类审批→下发；审批门必须硬拦。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import hq_case as hc  # noqa: E402


def _fake_chat(agent, system, user, base=hc.BASE, timeout=120):
    return {"text": agent + "：意见/方案", "binding": "glm/glm-5.3", "latency_ms": 1, "degraded": False}


def _new(tmp_path):
    c = hc.new_case("测试议题", [], case_id="CASE-TEST-001", base=tmp_path)
    assert c["stage"] == "DRAFT"
    return c


def test_full_flow_and_approval_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(hc, "_chat", _fake_chat)
    c = _new(tmp_path)
    c = hc.aggregate(c); assert c["stage"] == "AGGREGATED" and c["a0_summary"]
    c = hc.review(c); assert c["stage"] == "REVIEWED" and len(c["opinions"]) == 5
    c = hc.meeting(c, tmp_path / "meetings"); assert c["stage"] == "MEETING" and c["meeting_id"]
    # 未审批不得下发
    try:
        hc.dispatch(c, meeting_dir=tmp_path / "meetings", submit=lambda *a, **k: {"ok": True, "id": "x"})
        assert False
    except ValueError as e:
        assert "human_approval_required" in str(e)
    c = hc.propose(c); assert c["stage"] == "PROPOSED" and c["proposal"]
    c = hc.approve(c, "老田", "同意"); assert c["stage"] == "APPROVED"
    calls = []
    c = hc.dispatch(c, "DATA1", meeting_dir=tmp_path / "meetings", submit=lambda *a, **k: calls.append(a) or {"ok": True, "id": "req-1"})
    assert c["stage"] == "DISPATCHED" and calls


def test_review_incomplete_stays_aggregated(tmp_path, monkeypatch):
    def flaky(agent, system, user, base=hc.BASE, timeout=120):
        if agent == "QA1" or agent == "HUANGZHONG":
            raise RuntimeError("timeout")
        return _fake_chat(agent, system, user, base, timeout)
    monkeypatch.setattr(hc, "_chat", flaky)
    c = _new(tmp_path)
    c = hc.aggregate(c)
    c = hc.review(c)
    assert c["stage"] == "AGGREGATED"
    assert any(o.get("error") for o in c["opinions"])


def test_no_dispatch_without_meeting_approval(tmp_path):
    c = _new(tmp_path)
    try:
        hc.dispatch(c)
        assert False
    except ValueError as e:
        assert "human_approval_required" in str(e)
