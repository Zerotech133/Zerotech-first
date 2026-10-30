# -*- coding: utf-8 -*-
"""Hermes orchestrator：A0 规划 → 是否执行 → OH 只执行。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLAT = ROOT / "agent_platform"
if str(PLAT) not in sys.path:
    sys.path.insert(0, str(PLAT))

from legion_command import hermes_orchestrator as H  # noqa: E402
from legion_command import openhands_engine as OH  # noqa: E402


def test_parse_plan_json_and_fallback():
    assert H.parse_plan('{"need_execute": false, "plan": "直接回答"}') == {
        "need_execute": False, "plan": "直接回答"}
    p = H.parse_plan('```json\n{"need_execute": true, "plan": "改文件"}\n```')
    assert p["need_execute"] is True and p["plan"] == "改文件"
    p = H.parse_plan("不是 JSON 的自由回答")
    assert p["need_execute"] is True and "自由回答" in p["plan"]


def test_answer_does_not_start_oh(monkeypatch):
    monkeypatch.setattr(H, "ask_hermes", lambda *a, **k: {
        "ok": True, "binding": "glm/glm-5.3",
        "data": {"text": json.dumps(
            {"need_execute": False, "plan": "这是解释，不需要改仓库"})}})
    called = {"n": 0}
    monkeypatch.setattr(OH, "start", lambda *a, **k: called.__setitem__("n", 1))
    r = H.orchestrate("解释一下 JEV", agent="A0")
    assert r["ok"] and r["mode"] == "answer" and r["need_execute"] is False
    assert called["n"] == 0


def test_execute_passes_plan_to_oh(monkeypatch):
    monkeypatch.setattr(H, "ask_hermes", lambda *a, **k: {
        "ok": True, "binding": "glm/glm-5.3",
        "data": {"text": json.dumps(
            {"need_execute": True, "plan": "改 agent_platform/x.py 并跑测试"})}})
    seen = {}

    def fake_start(message, **kwargs):
        seen["message"] = message
        seen.update(kwargs)
        return {"ok": True, "conversation_id": "cid-1", "task": kwargs.get("task"),
                "worktree": "/tmp/wt", "model": "glm-5.3"}

    monkeypatch.setattr(OH, "start", fake_start)
    r = H.orchestrate("修复 bug", task="T-1", model="glm-5.3", agent="A0")
    assert r["ok"] and r["mode"] == "executed"
    assert "改 agent_platform/x.py" in seen["message"]
    assert "修复 bug" in seen["message"]
    assert seen["agent"] == "A0" and seen["task"] == "T-1"
