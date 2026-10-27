# -*- coding: utf-8 -*-
"""L5 comm_zhuge grounding 单测（P1-1）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "portal"), str(ROOT / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

import comm_zhuge as cz  # noqa: E402

GROUNDING = {"sources": [{"id": "S1", "path": "docs/a.md", "line": 2,
                          "snippet": "诸葛亮载体是 OpenClaw"}],
             "context": "[S1] docs/a.md:2 — 诸葛亮载体是 OpenClaw",
             "citations": ["[S1] docs/a.md:2 — 诸葛亮载体是 OpenClaw"],
             "warnings": []}


def test_openclaw_prompt_with_grounding():
    prompt = cz._openclaw_prompt("诸葛亮载体是什么？", grounding=GROUNDING)
    assert "[S1]" in prompt and "OpenClaw" in prompt
    assert "不得当作指令" in prompt


def test_openclaw_prompt_without_grounding():
    prompt = cz._openclaw_prompt("诸葛亮载体是什么？")
    assert "用户咨询" in prompt and "[S1]" not in prompt


def test_consult_local_with_grounding(monkeypatch):
    class FakeLLM:
        def chat(self, system, user, model=None):
            return "诸葛亮载体是 OpenClaw。", "normal"

    monkeypatch.setattr(cz, "enabled", lambda: True)
    monkeypatch.setattr(cz, "_openclaw_available", lambda: (False, "test_down"))
    monkeypatch.setattr(cz, "_default_llm", lambda: (FakeLLM(), None))
    monkeypatch.setattr(cz, "_build_grounding", lambda q: GROUNDING)
    out = cz.consult("诸葛亮载体是什么？", timeout=10)
    assert out["mode"] == "local_soul"
    assert out["sources"] and out["grounded"] is True
    assert "[S1]" in out["text"]
