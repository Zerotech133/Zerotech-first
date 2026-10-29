# -*- coding: utf-8 -*-
"""P1 接线：RAG auto / decisions→memory seed / MCP 只读工具存在。"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name + "_test", str(TOOLS / (name + ".py")))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_rag_auto_only_with_index(tmp_path, monkeypatch):
    g = _load("zhuge_grounding")
    monkeypatch.setenv("ZHUGE_MCP_REPO", str(tmp_path))
    assert g._rag_enabled() is False
    idx = tmp_path / "work" / "p1" / "rag" / "index.json"
    idx.parent.mkdir(parents=True)
    idx.write_text("{}", encoding="utf-8")
    assert g._rag_enabled() is True
    monkeypatch.setenv("ZHUGE_RAG", "off")
    assert g._rag_enabled() is False


def test_memory_seed_apply(tmp_path, monkeypatch):
    seed = _load("zhuge_memory_seed")
    repo = tmp_path
    decisions = repo / "decisions.jsonl"
    rec = {"id": "DR-20261029-01", "title": "测试决策",
           "recommendation": "保留规则护栏，不直接执行。", "status": "approved",
           "decided_by": "田丰"}
    decisions.write_text(json.dumps({"event": "created", "id": rec["id"], "record": rec},
                                    ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setenv("ZHUGE_MEMORY_REPO", str(repo))
    rc = seed.main(["--decisions", str(decisions), "--apply"])
    assert rc == 0
    mem = repo / "work" / "p1" / "memory" / "memory.jsonl"
    assert mem.is_file()
    rows = [json.loads(x) for x in mem.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert any("测试决策" in r.get("text", "") and r.get("kind") == "decision" for r in rows)


def test_mcp_server_selftest_module_exists():
    assert (TOOLS / "zhuge_mcp_server.py").is_file()
    text = (TOOLS / "zhuge_mcp_server.py").read_text(encoding="utf-8")
    for name in ("tasks_read", "org_read", "docs_search", "health_read", "brief_read", "web_fetch_tool"):
        assert "def " + name in text
