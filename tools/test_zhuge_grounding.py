# -*- coding: utf-8 -*-
"""zhuge_grounding 单测（P1-1）。离线：临时 docs + mock post。"""
from __future__ import annotations

import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import zhuge_grounding as g  # noqa: E402


def make_repo(tmp_path: Path, with_docs: bool = True) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    if with_docs:
        (repo / "docs" / "legion").mkdir(parents=True)
        (repo / "docs" / "legion" / "a.md").write_text(
            "# 诸葛亮\n\n诸葛亮当前 LLM 载体是 OpenClaw agent-config；8795 网关提供 /llm/chat。\n",
            encoding="utf-8")
        (repo / "docs" / "legion" / "b.md").write_text(
            "# 质询\n\n质询卡账本 cards.jsonl 记录证据与状态。\n", encoding="utf-8")
    return repo


def test_retrieve_finds_sources(monkeypatch, tmp_path):
    repo = make_repo(tmp_path)
    monkeypatch.setenv("ZHUGE_MCP_REPO", str(repo))
    src = g.retrieve("诸葛亮当前 LLM 载体是什么？")
    assert src and src[0]["id"] == "S1"
    assert "OpenClaw" in src[0]["snippet"]


def test_retrieve_empty(monkeypatch, tmp_path):
    repo = make_repo(tmp_path, with_docs=False)
    monkeypatch.setenv("ZHUGE_MCP_REPO", str(repo))
    assert g.retrieve("诸葛亮") == []


def test_build_grounding_and_prompt(monkeypatch, tmp_path):
    repo = make_repo(tmp_path)
    monkeypatch.setenv("ZHUGE_MCP_REPO", str(repo))
    grounded = g.build_grounding("诸葛亮载体是什么？")
    assert grounded["sources"] and grounded["citations"]
    prompt = g.grounded_prompt("诸葛亮载体是什么？", grounded)
    assert "[S1]" in prompt and "OpenClaw" in prompt
    assert "每个事实结论后用 [S#]" in prompt


def test_ensure_citations_appends_only_when_missing():
    sources = [{"id": "S1", "path": "docs/a.md", "line": 2, "snippet": "事实"}]
    assert g.ensure_citations("带 [S1] 的答案", sources) == "带 [S1] 的答案"
    out = g.ensure_citations("无引用答案", sources)
    assert "参考：" in out and "[S1]" in out


def test_grounded_chat_with_mock(monkeypatch, tmp_path):
    repo = make_repo(tmp_path)
    monkeypatch.setenv("ZHUGE_MCP_REPO", str(repo))
    monkeypatch.setattr(g, "_post_chat", lambda *a, **k: {
        "ok": True, "text": "载体是 OpenClaw。", "carrier": "deepseek/deepseek-chat",
        "relay": True, "status": "normal", "error": None})
    out = g.grounded_chat("诸葛亮载体是什么？")
    assert out["ok"] and out["grounded"] is True
    assert out["sources"] and "[S1]" in out["text"]
