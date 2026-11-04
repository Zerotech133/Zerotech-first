# -*- coding: utf-8 -*-
"""RAG 基线评测测试（task-164，mock/离线：不真调 ollama/LLM）。

覆盖：
  1) 问题构造：卡题+单元题、题型分布、gold 文档 id；
  2) 指标函数：recall@k / MRR / cosine_top / 引用命中 / 一致性；
  3) evaluate 全流程（mock embed/chat）：recall@5=1、MRR=1、引用率=1、schema 字段；
  4) 超时抽样：budget 超限时停止并记录 sampled_note；
  5) 拒答/一致性边界。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "kb_corpus" / "rag_baseline_eval.py"

_spec = importlib.util.spec_from_file_location("rag_eval_test", SCRIPT)
m = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = m
_spec.loader.exec_module(m)  # type: ignore[union-attr]


def make_docs():
    units = [{"doc_id": f"U:u{i}", "kind": "unit", "text": f"第{i}条知识单元：均线粘合度用于判断趋势蓄势。",
              "concept": "", "source": f"f{i}.md", "project": f"p{i % 2}"} for i in range(5)]
    cards = [{"doc_id": f"C:{i:03d}", "kind": "card", "text": f"卡片{i}：煤电兜底价值；依据：研报摘要。",
              "concept": f"概念{i}", "source": f"《书{i}》 p1", "project": "cards"} for i in range(3)]
    return units + cards


def test_build_questions():
    docs = make_docs()
    qs = m.build_questions(docs, 50)
    assert len(qs) == 8  # 3 卡 + 5 单元
    types = {q["type"] for q in qs}
    assert types == {"card_concept", "card_evidence", "unit_howto", "unit_fact"}
    for q in qs:
        assert q["qid"].startswith("Q")
        assert q["question"] and q["gold_doc_ids"] and q["gold_text"]
        assert len(q["gold_doc_ids"]) == 1
    # gold 指向真实文档
    ids = {d["doc_id"] for d in docs}
    assert all(q["gold_doc_ids"][0] in ids for q in qs)


def test_metrics_functions():
    assert m.recall_at(["a", "b", "c"], ["c"], 5) == 1.0
    assert m.recall_at(["a", "b", "c"], ["z"], 5) == 0.0
    assert m.mrr_score(["a", "b", "c"], ["b"]) == 0.5
    assert m.mrr_score(["a"], ["z"]) == 0.0
    assert m.consistency("均线粘合度判断趋势", "均线粘合度判断趋势") == 1.0
    assert m.consistency("", "abc") == 0.0
    # cosine_top 排序
    docs = [{"doc_id": "d0", "kind": "unit", "text": "x", "source": "s"},
            {"doc_id": "d1", "kind": "unit", "text": "y", "source": "s"}]
    hits = m.cosine_top([1.0, 0.0], [[1.0, 0.0], [0.0, 1.0]], docs, 2)
    assert hits[0]["doc_id"] == "d0" and hits[0]["score"] == 1.0
    assert m.citation_hit("答案[1]", [{"source": "f.md"}]) is True
    assert m.citation_hit("无引用答案", [{"source": "f.md"}]) is False


def test_evaluate_mock():
    docs = make_docs()
    questions = m.build_questions(docs, 50)

    def embed_fn(texts):
        out = []
        for t in texts:
            # 问题文本中能找到 gold 文档 concept 或特征 → 指向对应向量
            vec = [0.0] * (len(docs) + 1)
            matched = False
            for i, d in enumerate(docs):
                key = d.get("concept") or (d["text"].split("：")[0])
                if key and key in t:
                    vec[i] = 1.0
                    matched = True
                    break
            if not matched:
                for i, d in enumerate(docs):
                    if d["text"][:8] in t:
                        vec[i] = 1.0
                        break
                else:
                    vec[-1] = 1.0
            out.append(vec)
        return out

    payload = m.evaluate(write=False, docs=docs, questions=questions,
                         embed_fn=embed_fn, chat_fn=lambda prompt: "依据资料 [1] 回答。")
    agg = payload["aggregate"]
    assert agg["questions_total"] == len(questions) and agg["evaluated"] == len(questions)
    assert payload["sampled_note"] is None
    assert agg["recall@5"] == 1.0 and agg["recall@10"] == 1.0 and agg["mrr"] == 1.0
    assert agg["citation_rate"] == 1.0
    for key in ("schema_version", "method", "sources", "aggregate", "results", "limitations"):
        assert key in payload


def test_timeout_sampling():
    docs = make_docs()
    questions = m.build_questions(docs, 50)
    payload = m.evaluate(write=False, docs=docs, questions=questions,
                         embed_fn=lambda texts: [[0.0] * len(docs) for _ in texts],
                         chat_fn=lambda prompt: "不知道", budget_s=-1)
    assert payload["sampled_note"] and "抽样" in payload["sampled_note"]
    assert payload["aggregate"]["evaluated"] == len(questions), "检索仍应全量"
    assert payload["aggregate"]["generation_evaluated"] == 0


def test_refusal_detection():
    docs = make_docs()
    questions = m.build_questions(docs, 50)
    payload = m.evaluate(write=False, docs=docs, questions=questions,
                         embed_fn=lambda texts: [[0.0] * len(docs) for _ in texts],
                         chat_fn=lambda prompt: "资料未提及", budget_s=60)
    assert payload["aggregate"]["refusal_rate"] == 1.0
    assert payload["aggregate"]["citation_rate"] == 0.0
