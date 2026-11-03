# -*- coding: utf-8 -*-
"""日报场景：非 glm 主绑失败可降级到 glm；会议场景仍不透传假降级。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent_platform"))
from legion_command import serve as srv  # noqa: E402


def test_report_purpose_allows_non_glm_fallback():
    calls = []

    def chat(binding, system, user):
        calls.append(binding)
        if binding == "deepseek/deepseek-v4-pro":
            return {"ok": False, "error": {"message": "finish=length"}}
        return {"ok": True, "data": {"text": "glm fallback", "binding_resolved": binding}}

    env, attempts = srv._seat_chat(chat, "deepseek/deepseek-v4-pro", ["ollama/qwen3.5:9b"],
                                   "s", "u", allow_non_glm_fallback=True)
    assert env["ok"] and env["degraded"] is True
    assert env["fallback_from"] == "deepseek/deepseek-v4-pro"
    assert attempts[0] == "deepseek/deepseek-v4-pro" and "glm/glm-5.3" in attempts


def test_meeting_default_does_not_fallback_non_glm():
    calls = []

    def chat(binding, system, user):
        calls.append(binding)
        return {"ok": False, "error": {"message": "down"}}

    env, attempts = srv._seat_chat(chat, "deepseek/deepseek-v4-pro", ["ollama/qwen3.5:9b"], "s", "u")
    assert env["ok"] is False and attempts == ["deepseek/deepseek-v4-pro"]
