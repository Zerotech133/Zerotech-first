# -*- coding: utf-8 -*-
"""zhuge_memory v0 单测（离线，临时仓库）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import zhuge_memory as m  # noqa: E402


def use_repo(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("ZHUGE_MEMORY_REPO", str(repo))
    return repo


def test_remember_and_recall(monkeypatch, tmp_path):
    use_repo(monkeypatch, tmp_path)
    out = m.remember("lesson", "BM25 检索需要同义词扩展，否则请求体类问题漏召回",
                     tags=["rag", "bm25"], source="test", confidence="high")
    assert out["ok"] is True and out["duplicate"] is False
    hits = m.recall("BM25 同义词 检索", k=3)
    assert hits and "同义词" in hits[0]["text"]
    assert hits[0]["kind"] == "lesson"


def test_dedup(monkeypatch, tmp_path):
    use_repo(monkeypatch, tmp_path)
    a = m.remember("fact", "8795 端口是本地网关")
    b = m.remember("fact", "8795 端口是本地网关")
    assert a["ok"] and b["ok"] and b["duplicate"] is True


def test_invalid_kind_and_empty(monkeypatch, tmp_path):
    use_repo(monkeypatch, tmp_path)
    assert m.remember("bad", "x")["ok"] is False
    assert m.remember("fact", "  ")["ok"] is False


def test_recall_empty(monkeypatch, tmp_path):
    use_repo(monkeypatch, tmp_path)
    assert m.recall("任何问题") == []


def test_prune_expired(monkeypatch, tmp_path):
    repo = use_repo(monkeypatch, tmp_path)
    path = repo / "work" / "p1" / "memory" / "memory.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"id": "MEM-X", "kind": "fact", "text": "旧记忆",
                                "ts": 1, "expires_at": 2}, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    out = m.prune_expired()
    assert out["pruned"] == 1
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert any(r.get("event") == "expired" for r in rows)


def test_load_skips_bad(monkeypatch, tmp_path):
    repo = use_repo(monkeypatch, tmp_path)
    path = repo / "work" / "p1" / "memory" / "memory.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"id":"A","kind":"fact","text":"x","ts":1}\n{broken\n', encoding="utf-8")
    rows = m._load()
    assert len(rows) == 1 and rows[0]["id"] == "A"
