# -*- coding: utf-8 -*-
"""P1X：RAG 自动重建 + 引用评分契约。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))


def _load(name: str):
    spec = importlib.util.spec_from_file_location("_p1x_" + name, str(TOOLS / (name + ".py")))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_ensure_fresh_builds_when_missing(tmp_path):
    rag = _load("zhuge_rag")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# 诸葛亮\n\n载体是 DeepSeek。\n", encoding="utf-8")
    out = rag.ensure_fresh(tmp_path)
    assert out["ok"] and out["rebuilt"] is True
    idx = tmp_path / "work" / "p1" / "rag" / "index.json"
    assert idx.is_file()


def test_ensure_fresh_skips_current(tmp_path):
    rag = _load("zhuge_rag")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# 诸葛亮\n\n内容\n", encoding="utf-8")
    rag.build_index(tmp_path, out=tmp_path / "work" / "p1" / "rag" / "index.json")
    out = rag.ensure_fresh(tmp_path)
    assert out["ok"] and out["rebuilt"] is False


def test_citation_score():
    r = _load("zhuge_reflector")
    sources = [{"id": "S1"}, {"id": "S2"}]
    out = r.citation_score("结论 [S1]，补充 [S2]。", sources)
    assert out["score"] is not None and out["valid"] == 2
    none = r.citation_score("无来源结论", [])
    assert none["score"] is None
