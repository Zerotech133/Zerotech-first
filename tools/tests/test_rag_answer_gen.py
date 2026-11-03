# -*- coding: utf-8 -*-
"""RAG 生成侧优化测试（task-176，离线/mock：不真调 ollama/LLM）。

覆盖：
  1) 三策略 prompt：A/B/C 差异与关键约束（仅用资料/每句末引用/拒答/≤180字/证据摘录）；
  2) 证据摘录：≤5 条、编号合法、与问题相关的句子优先；
  3) 指标聚合：引用正确率/groundedness/拒答/长度等计算自洽；
  4) evaluate（mock embed/chat）：A/B/C 三策略结果、deltas、best、schema；
  5) 超时抽样：预算超限记录 sampled_note 且三策略同题补齐。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "kb_corpus" / "rag_answer_gen.py"

_spec = importlib.util.spec_from_file_location("rag_gen_test", SCRIPT)
m = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = m
_spec.loader.exec_module(m)  # type: ignore[union-attr]

CTX = [
    {"doc_id": "U:u0", "text": "均线粘合度用于判断趋势蓄势。均线粘合后常出现方向选择。"},
    {"doc_id": "U:u1", "text": "煤电具有兜底价值与灵活调节能力。"},
]


def test_strategy_prompts():
    pa = m.build_prompt("A", "什么是均线粘合度？", CTX)
    pb = m.build_prompt("B", "什么是均线粘合度？", CTX)
    pc = m.build_prompt("C", "什么是均线粘合度？", CTX)
    assert "2-4 句话" in pa and "证据摘录" not in pa
    assert "每个事实句末" in pb and "资料不足" in pb and "180" in pb
    assert "证据摘录" in pc and "[1]" in pc
    assert pa != pb != pc


def test_extract_evidence():
    ev = m.extract_evidence("均线粘合度如何判断趋势？", CTX, max_items=5)
    assert 1 <= len(ev) <= 5
    assert all(1 <= e["n"] <= len(CTX) for e in ev)
    assert all(e["text"] for e in ev)
    assert "均线粘合" in ev[0]["text"], "相关证据句应排第一"


def test_aggregate_metrics():
    res = [
        {"answerable": True, "citation_ok": True, "citation_valid": True, "groundedness": 0.8,
         "gold_similarity": 0.5, "refused": False, "overclaim": False, "length_anomaly": False, "length": 50},
        {"answerable": True, "citation_ok": False, "citation_valid": False, "groundedness": 0.2,
         "gold_similarity": 0.1, "refused": False, "overclaim": True, "length_anomaly": False, "length": 60},
        {"answerable": False, "citation_ok": True, "citation_valid": True, "groundedness": 0.1,
         "gold_similarity": None, "refused": True, "overclaim": False, "length_anomaly": False, "length": 20},
    ]
    a = m.aggregate(res)
    assert a["citation_accuracy"] == round(2 / 3, 4)
    assert a["groundedness"] == round((0.8 + 0.2 + 0.1) / 3, 4)
    assert a["correct_refusal_rate"] == 1.0 and a["over_refusal_rate"] == 0.0
    assert a["overclaim_rate"] == round(1 / 3, 4)
    assert a["answerable_n"] == 2 and a["unanswerable_n"] == 1


def test_evaluate_mock():
    docs = [{"doc_id": "U:u0", "kind": "unit", "text": "均线粘合度用于判断趋势蓄势。", "source": "a.md"},
            {"doc_id": "U:u1", "kind": "unit", "text": "煤电兜底价值与调节能力。", "source": "b.md"}]
    questions = [
        {"qid": "A01", "type": "card_concept", "answerable": True, "question": "什么是均线粘合度？",
         "gold_doc_ids": ["U:u0"], "gold_text": "均线粘合度用于判断趋势蓄势。"},
        {"qid": "U01", "type": "unanswerable", "answerable": False, "question": "如何申请签证？",
         "gold_doc_ids": [], "gold_text": ""},
    ]

    def embed_fn(texts):
        out = []
        for t in texts:
            vec = [0.0, 0.0]
            if "均线" in t:
                vec[0] = 1.0
            elif "煤电" in t:
                vec[1] = 1.0
            out.append(vec)
        return out

    def chat_fn(prompt):
        return "资料未提及，无法回答。" if "签证" in prompt else "均线粘合度用于判断趋势蓄势 [1]"

    payload = m.evaluate(write=False, docs=docs, questions=questions, embed_fn=embed_fn, chat_fn=chat_fn)
    assert payload["schema_version"] == "rag_gen_improve.v1"
    for s in ("A", "B", "C"):
        assert s in payload["aggregate"] and payload["aggregate"][s]["evaluated"] == 2
    assert set(payload["deltas_vs_A"]) == {"B-A", "C-A"}
    assert payload["best"] in ("A", "B", "C")
    assert payload["conclusion"] and payload["targets"]


def test_timeout_sampling():
    docs = [{"doc_id": "U:u0", "kind": "unit", "text": "均线粘合度用于判断趋势蓄势。", "source": "a.md"}]
    questions = [{"qid": "A01", "type": "card_concept", "answerable": True, "question": "什么是均线粘合度？",
                  "gold_doc_ids": ["U:u0"], "gold_text": "均线粘合度用于判断趋势蓄势。"}]
    payload = m.evaluate(write=False, docs=docs, questions=questions,
                        embed_fn=lambda texts: [[1.0] for _ in texts],
                        chat_fn=lambda prompt: "回答 [1]", budget_s=-1)
    assert payload["sampled_note"] and "超预算" in payload["sampled_note"]
    for s in ("A", "B", "C"):
        assert payload["aggregate"][s]["evaluated"] == 1, "三策略均按同题补齐"
