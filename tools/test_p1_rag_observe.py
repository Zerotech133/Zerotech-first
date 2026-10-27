# -*- coding: utf-8 -*-
"""p1_rag_observe 单测（离线，临时 JSONL）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import p1_rag_observe as o  # noqa: E402


def test_summarize_empty():
    res = o.summarize([])
    assert res["total"] == 0 and res["empty_rate"] == 0
    assert res["latency_ms"]["p50"] == 0.0


def test_summarize_mixed():
    rows = [
        {"rag_enabled": True, "source_count": 3, "elapsed_ms": 100},
        {"rag_enabled": True, "source_count": 0, "elapsed_ms": 300},
        {"rag_enabled": False, "source_count": 2, "elapsed_ms": 200},
    ]
    res = o.summarize(rows)
    assert res["total"] == 3
    assert res["rag_enabled"] == 2 and res["rag_enabled_rate"] == 0.6667
    assert res["empty_retrieval"] == 1 and res["empty_rate"] == 0.3333
    assert res["source_count_histogram"] == {"0": 1, "2": 1, "3": 1}
    assert res["latency_ms"]["p50"] == 200.0
    assert res["latency_ms"]["max"] == 300.0


def test_load_skips_bad_lines(tmp_path):
    path = tmp_path / "usage.jsonl"
    path.write_text(
        "\n".join([json.dumps({"rag_enabled": True, "source_count": 1, "elapsed_ms": 5}),
                   "{bad json", ""]), encoding="utf-8")
    rows = o.load(path)
    assert len(rows) == 1 and rows[0]["source_count"] == 1


def test_load_missing(tmp_path):
    assert o.load(tmp_path / "nope.jsonl") == []
