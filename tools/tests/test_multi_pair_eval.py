# -*- coding: utf-8 -*-
"""multi_pair_eval 纯函数/报告契约测试（不调真 LLM、不检索）。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PATH = REPO / "tools" / "kb_corpus" / "multi_pair_eval.py"


def _load():
    spec = importlib.util.spec_from_file_location("multi_pair_eval_under_test", PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


e = _load()


def _item(i, source, answerable=True):
    return {"id": f"{source}_{i}", "source": source, "type": "unit_fact",
            "question": f"q{i}", "answerable": answerable}


def test_select_questions_20_is_stratified_and_unique():
    items = ([_item(i, "w13", False) for i in range(4)] +
             [_item(i, "w13") for i in range(6)] +
             [_item(i, "heldout") for i in range(5)] +
             [_item(i, "citation_clean") for i in range(5)])
    # 避免 id 冲突：unanswerable 使用独立 id
    for i, item in enumerate(items[:4]):
        item["id"] = f"unans_{i}"
    qs = e.select_questions(items, 20)
    assert len(qs) == 20
    ids = [x["id"] for x in qs]
    assert len(set(ids)) == 20
    assert sum(1 for x in qs if not x["answerable"]) == 4
    assert sum(1 for x in qs if x["source"] == "heldout") == 5
    assert sum(1 for x in qs if x["source"] == "citation_clean") == 5


def test_select_questions_respects_limit():
    items = ([_item(i, "w13", False) for i in range(4)] +
             [_item(i, "w13") for i in range(6)] +
             [_item(i, "heldout") for i in range(5)] +
             [_item(i, "citation_clean") for i in range(5)])
    for i, item in enumerate(items[:4]):
        item["id"] = f"unans_{i}"
    qs = e.select_questions(items, 5)
    assert len(qs) == 5
    assert len({x["id"] for x in qs}) == 5


def test_verdict_prefers_correct_refusal_on_unanswerable():
    base = {"citations": 0, "citation_valid_count": 0, "citation_invalid_count": 0,
            "guard_passed": False, "refusal": True, "fallback": False,
            "error": None, "elapsed": 1.0, "answer_chars": 16}
    row = {"answerable": False, "v4d": dict(base), "ollama": dict(base, refusal=False)}
    assert e._verdict(row) == "v4d"


def test_verdict_penalizes_wrong_answer_on_answerable():
    good = {"citations": 2, "citation_valid_count": 2, "citation_invalid_count": 0,
            "guard_passed": True, "refusal": False, "fallback": False,
            "error": None, "elapsed": 1.0, "answer_chars": 100}
    bad = {"citations": 0, "citation_valid_count": 0, "citation_invalid_count": 0,
           "guard_passed": False, "refusal": True, "fallback": False,
           "error": None, "elapsed": 1.0, "answer_chars": 16}
    row = {"answerable": True, "v4d": good, "ollama": bad}
    assert e._verdict(row) == "v4d"


def _fake_row(verdict="tie"):
    side = {"answer": "x", "answer_chars": 1, "elapsed": 1.0, "citations": 1,
            "confidence": "medium", "refusal": False, "fallback": False,
            "retry_used": False, "guard_passed": True, "guard_issues": [],
            "citation_valid_count": 1, "citation_invalid_count": 0,
            "citation_resolvable_rate": 1.0, "distinct_cited_units": 1,
            "inference_ratio": 0.0, "usage": {}, "cost_cny": 0.0, "error": None}
    return {"id": "q1", "source": "w13", "type": "unit_fact", "answerable": True,
            "question": "q", "retrieval_seconds": 0.1, "clusters": 1, "units": 1,
            "v4d": dict(side), "ollama": dict(side), "verdict": verdict}


def test_build_report_aggregates_and_keeps_rows():
    rows = [_fake_row("tie"), dict(_fake_row("v4d"), id="q2")]
    rep = e.build_report(rows, {"limit": 2, "k": 6, "citation_retry": True})
    assert rep["aggregate"]["v4d"]["n"] == 2
    assert rep["aggregate"]["ollama"]["n"] == 2
    assert rep["aggregate"]["verdicts"] == {"v4d": 1, "ollama": 0, "tie": 1}
    assert len(rep["rows"]) == 2


def test_write_md_contains_aggregate_and_per_question():
    rep = e.build_report([_fake_row("v4d")], {"limit": 1, "k": 6, "citation_retry": True})
    md = e.write_md(rep)
    assert "v4d vs Ollama" in md
    assert "逐题结果" in md
    assert "| 1 | q1 |" in md
    assert "启发式偏好" in md
