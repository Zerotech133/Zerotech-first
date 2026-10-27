# -*- coding: utf-8 -*-
"""zhuge_rag BM25 v0 单测（离线，临时 docs）。"""
from __future__ import annotations

import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import zhuge_rag as r  # noqa: E402


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "a.md").write_text(
        "# 诸葛亮\n\n诸葛亮当前 LLM 载体是 OpenClaw agent-config；8795 提供 /llm/chat。\n",
        encoding="utf-8")
    (repo / "docs" / "b.md").write_text(
        "# 质询\n\n质询卡账本 cards.jsonl 记录证据与状态，board=zhugeliang-review。\n",
        encoding="utf-8")
    return repo


def test_tokenize_cjk_and_alnum():
    tokens = r.tokenize("诸葛亮 OpenClaw 8795 /llm/chat")
    assert "OpenClaw".lower() in tokens or "openclaw" in tokens
    assert "8795" in tokens
    assert any("诸葛" in t for t in tokens)


def test_build_and_search(monkeypatch, tmp_path):
    repo = make_repo(tmp_path)
    monkeypatch.setenv("ZHUGE_RAG_REPO", str(repo))
    built = r.build_index(root=repo)
    assert built["ok"] and built["chunks"] >= 2
    hits = r.search("诸葛亮载体是什么？", k=2, root=repo)
    assert hits and hits[0]["path"] == "docs/a.md"
    assert hits[0]["id"] == "S1"


def test_search_returns_empty_for_nonsense(monkeypatch, tmp_path):
    repo = make_repo(tmp_path)
    monkeypatch.setenv("ZHUGE_RAG_REPO", str(repo))
    r.build_index(root=repo)
    assert r.search("zzzzqqqq", k=2, root=repo) == []


def test_no_docs_index_builds_empty(monkeypatch, tmp_path):
    repo = tmp_path / "empty"
    repo.mkdir()
    monkeypatch.setenv("ZHUGE_RAG_REPO", str(repo))
    built = r.build_index(root=repo)
    assert built["chunks"] == 0
    assert r.search("诸葛亮", k=2, root=repo) == []


def test_check_index(monkeypatch, tmp_path):
    repo = make_repo(tmp_path)
    monkeypatch.setenv("ZHUGE_RAG_REPO", str(repo))
    r.build_index(root=repo)
    info = r.check(root=repo)
    assert info["ok"] and info["exists"] is True and info["chunk_count"] >= 2


def test_query_expansion_helps_request_body(monkeypatch, tmp_path):
    repo = tmp_path / "repo2"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "api.md").write_text(
        "# 请求体\n\n8795 /llm/chat 请求体字段：binding、user、system。\n",
        encoding="utf-8")
    monkeypatch.setenv("ZHUGE_RAG_REPO", str(repo))
    r.build_index(root=repo)
    hits = r.search("8795 请求体需要哪些字段？", k=2, root=repo)
    assert hits and "binding" in hits[0]["snippet"]
    assert "binding" in r.expand_query("请求体需要哪些字段？")
