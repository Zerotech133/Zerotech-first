# -*- coding: utf-8 -*-
"""答案侧评测测试（task-171，离线/mock：不真调 ollama/LLM）。

覆盖：
  1) 评测集：40 题 = 30 可答（分题型）+ 10 库外不可答，字段完整；
  2) 确定性指标函数：引用解析/groundedness/gold 相似度/拒答/过度声明/长度异常；
  3) judge_one：可答引用 gold 正确、不可答正确拒答、编造引用/可答拒答判错；
  4) evaluate（mock embed/chat）：aggregate/schema/targets/样例；超时抽样注明。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "kb_corpus" / "answer_eval.py"

_spec = importlib.util.spec_from_file_location("answer_eval_test", SCRIPT)
m = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = m
_spec.loader.exec_module(m)  # type: ignore[union-attr]


def test_build_questions():
    qs = m.build_questions(m.W10MOD.load_questions(), 30)
    assert len(qs) == 40
    assert sum(1 for q in qs if q["answerable"]) == 30
    assert sum(1 for q in qs if not q["answerable"]) == 10
    for q in qs:
        assert q["qid"] and q["question"]
        if q["answerable"]:
            assert q["gold_doc_ids"]
        else:
            assert q["gold_doc_ids"] == [] and q["unable_reason"]
    assert len({q["qid"] for q in qs}) == 40


def test_metric_functions():
    assert m.cite_indices("依据[1][3]") == [1, 3]
    assert m.cite_indices("无引用") == []
    ctx = ["均线粘合度用于判断趋势蓄势", "煤电兜底价值与调节能力"]
    assert m.groundedness("均线粘合度用于判断趋势蓄势", ctx) == 1.0
    assert m.groundedness("完全无关的回答内容", ctx) == 0.0
    assert m.gold_similarity("均线粘合度判断趋势", "均线粘合度判断趋势") == 1.0
    assert m.gold_similarity("", "abc") == 0.0
    assert m.is_refusal("资料未提及") and m.is_refusal("")
    assert not m.is_refusal("均线粘合度用于判断趋势。")
    assert m.overclaim_hit("这只票必涨") and not m.overclaim_hit("注意风险")
    assert m.length_anomaly("太短") and m.length_anomaly("长" * 401)
    assert not m.length_anomaly("这是一段长度正常的回答内容，用于测试长度异常判断。")


def test_judge_one():
    ctx = [{"doc_id": "U:u0", "text": "均线粘合度用于判断趋势蓄势。"},
           {"doc_id": "U:u1", "text": "煤电兜底价值与调节能力。"}]
    q_ans = {"qid": "A01", "type": "card_concept", "answerable": True,
             "question": "什么是均线粘合度？", "gold_doc_ids": ["U:u0"], "gold_text": "均线粘合度用于判断趋势蓄势。"}
    r = m.judge_one(q_ans, ctx, "均线粘合度用于判断趋势蓄势 [1]")
    assert r["citation_ok"] is True and r["citation_gold"] == ["U:u0"]
    assert r["groundedness"] > 0.8
    q_un = {"qid": "U01", "type": "unanswerable", "answerable": False,
            "question": "如何申请签证？", "gold_doc_ids": [], "gold_text": ""}
    r2 = m.judge_one(q_un, ctx, "资料未提及，无法回答。")
    assert r2["citation_ok"] is True and r2["refused"] is True
    r3 = m.judge_one(q_un, ctx, "签证需要准备材料 [1]。")
    assert r3["citation_ok"] is False
    r4 = m.judge_one(q_ans, ctx, "资料未提及")
    assert r4["citation_ok"] is False


def test_evaluate_mock():
    docs = [{"doc_id": "U:u0", "kind": "unit", "text": "均线粘合度用于判断趋势蓄势。", "source": "a.md"},
            {"doc_id": "U:u1", "kind": "unit", "text": "煤电兜底价值与调节能力。", "source": "b.md"}]
    questions = [
        {"qid": "A01", "type": "card_concept", "answerable": True, "question": "什么是均线粘合度？",
         "gold_doc_ids": ["U:u0"], "gold_text": "均线粘合度用于判断趋势蓄势。"},
        {"qid": "A02", "type": "unit_fact", "answerable": True, "question": "煤电的兜底价值是什么？",
         "gold_doc_ids": ["U:u1"], "gold_text": "煤电兜底价值与调节能力。"},
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
        if "签证" in prompt:
            return "资料未提及，无法回答。"
        return "均线粘合度用于判断趋势蓄势 [1]" if "均线" in prompt else "煤电兜底价值与调节能力 [1]"

    payload = m.evaluate(write=False, docs=docs, questions=questions, embed_fn=embed_fn, chat_fn=chat_fn)
    a = payload["aggregate"]
    assert payload["schema_version"] == "answer_eval.v1"
    assert a["evaluated"] == 3 and a["answerable_n"] == 2 and a["unanswerable_n"] == 1
    assert 0.0 <= a["citation_accuracy"] <= 1.0
    assert 0.0 <= a["groundedness"] <= 1.0
    assert a["correct_refusal_rate"] == 1.0
    for key in ("targets", "target_notes", "results", "limitations", "dataset"):
        assert key in payload
    assert set(payload["targets"]) >= {"citation_accuracy", "groundedness", "correct_refusal_rate"}


def test_timeout_sampling():
    docs = [{"doc_id": "U:u0", "kind": "unit", "text": "均线粘合度用于判断趋势蓄势。", "source": "a.md"}]
    questions = [{"qid": "A01", "type": "card_concept", "answerable": True, "question": "什么是均线粘合度？",
                  "gold_doc_ids": ["U:u0"], "gold_text": "均线粘合度用于判断趋势蓄势。"}]
    payload = m.evaluate(write=False, docs=docs, questions=questions,
                         embed_fn=lambda texts: [[1.0] for _ in texts],
                         chat_fn=lambda prompt: "回答 [1]", budget_s=-1)
    assert payload["sampled_note"] and "抽样" in payload["sampled_note"]
    assert payload["aggregate"]["generation_evaluated"] == 0
