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


def test_evaluate_no_traffic_info_only():
    res = o.evaluate(o.summarize([]), {"exists": True, "built_at": "2026-10-27"})
    assert res["alerts"][0]["code"] == "no_traffic"
    assert res["high"] == 0 and res["action_required"] is False


def test_evaluate_high_alerts():
    summary = {"total": 10, "rag_enabled_rate": 0.2, "empty_rate": 0.5,
               "source_count_avg": 0.5,
               "latency_ms": {"p95": 1200}}
    res = o.evaluate(summary, {"exists": True, "built_at": "2026-10-20"})
    codes = {a["code"] for a in res["alerts"]}
    assert {"rag_not_enabled", "empty_retrieval_high", "latency_p95_high",
            "source_count_low"} <= codes
    assert res["high"] >= 2 and res["action_required"] is True
    assert "ZHUGE_RAG" in res["rollback"]


def test_evaluate_healthy():
    summary = {"total": 10, "rag_enabled_rate": 1.0, "empty_rate": 0.1,
               "source_count_avg": 3.0, "latency_ms": {"p95": 120}}
    res = o.evaluate(summary, {"exists": True, "built_at": "2026-10-27"})
    assert res["alerts"] == [] and res["action_required"] is False


def test_evaluate_index_missing_stale():
    res = o.evaluate({"total": 5, "rag_enabled_rate": 1.0, "empty_rate": 0,
                      "source_count_avg": 2, "latency_ms": {"p95": 100}},
                     {"exists": False})
    assert any(a["code"] == "index_missing" for a in res["alerts"])


def test_since_filters_old_samples(tmp_path):
    path = tmp_path / "usage.jsonl"
    path.write_text("\n".join([
        json.dumps({"ts": "2026-10-27 14:00:00", "rag_enabled": False,
                    "source_count": 0, "elapsed_ms": 10}),
        json.dumps({"ts": "2026-10-27 15:10:30", "rag_enabled": True,
                    "source_count": 2, "elapsed_ms": 20}),
    ]), encoding="utf-8")
    assert len(o.load(path)) == 2
    rows = o.load(path, since="2026-10-27 15:10:00")
    assert len(rows) == 1 and rows[0]["rag_enabled"] is True
    summary = o.summarize(rows)
    res = o.evaluate(summary, {"exists": True, "built_at": "2026-10-27"})
    assert res["action_required"] is False
