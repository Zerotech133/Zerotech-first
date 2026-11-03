# -*- coding: utf-8 -*-
"""RAG 混合检索测试（task-168，mock/离线：不真调 ollama/LLM）。

覆盖：
  1) 分词：CJK bigram + 拉丁词；
  2) BM25：包含查询词的文档优先、空查询返回空；
  3) RRF：融合顺序/权重/公式自洽；
  4) 同义词/PRF 扩展：命中词表、排除原查询词；
  5) evaluate 全流程（mock embed）：变体对比/schema/选定变体/目标判定。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "kb_corpus" / "rag_hybrid_eval.py"

_spec = importlib.util.spec_from_file_location("rag_hybrid_test", SCRIPT)
m = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = m
_spec.loader.exec_module(m)  # type: ignore[union-attr]


def test_tokenize():
    toks = m.tokenize("均线系统 MACD背离")
    assert "均线" in toks and "线系" in toks and "系统" in toks
    assert "macd" in toks and "背离" in toks
    assert m.tokenize("") == []


def test_bm25():
    docs = ["均线粘合度用于判断趋势", "煤电兜底价值与调节能力", "MACD 背离与指标信号"]
    bm25 = m.BM25(docs)
    assert bm25.N == 3
    top = bm25.search("均线粘合度")
    assert top and top[0] == 0
    assert bm25.search("完全不存在的词zzz") == []


def test_rrf_fuse():
    fused = m.rrf_fuse([[0, 1, 2], [2, 0, 1]], [1.0, 1.0], k=60, topn=3)
    # idx0: 1/61 + 1/62 ≈ 0.03253；idx2: 1/63 + 1/61 ≈ 0.03226；idx1: 1/62 + 1/63 ≈ 0.03200
    assert fused == [0, 2, 1]
    # 权重倾斜：BM25 权重 10 → 以第一路顺序为主
    fused_w = m.rrf_fuse([[0, 1, 2], [2, 0, 1]], [10.0, 1.0], k=60, topn=3)
    assert fused_w[0] == 0 and fused_w[1] == 1


def test_expansion():
    syn = {"均线粘合": ["均线", "MA", "多头排列"]}
    extra = m.expand_by_synonyms("均线粘合如何判断", syn)
    assert "MA" in extra and "多头排列" in extra
    docs = ["均线粘合度用于判断趋势", "均线多头排列的确认", "无关内容"]
    bm25 = m.BM25(docs)
    top = bm25.search("均线粘合度")
    prf = m.expand_by_prf("均线粘合度", bm25, top, bm25.tf)
    assert isinstance(prf, list) and "均线" not in prf[:1]


def test_evaluate_mock():
    docs = [{"doc_id": "U:u0", "kind": "unit", "text": "均线粘合度用于判断趋势蓄势。", "source": "a.md"},
            {"doc_id": "U:u1", "kind": "unit", "text": "煤电兜底价值与灵活调节。", "source": "b.md"},
            {"doc_id": "C:000", "kind": "card", "text": "指标位置关系的核心要点与依据。", "source": "《书》 p1"}]
    questions = [{"qid": "Q1", "question": "「均线粘合度」的核心要点是什么？", "type": "card_concept",
                  "gold_doc_ids": ["U:u0"], "gold_text": docs[0]["text"]},
                 {"qid": "Q2", "question": "「指标位置关系」的依据是什么？", "type": "card_evidence",
                  "gold_doc_ids": ["C:000"], "gold_text": docs[2]["text"]}]

    def embed_fn(texts):
        out = []
        for t in texts:
            vec = [0.0] * len(docs)
            for i, d in enumerate(docs):
                key = d["text"][:6]
                if key in t:
                    vec[i] = 1.0
                    break
            if not any(vec):
                for i, d in enumerate(docs):
                    core = d["text"][:4]
                    if core in t:
                        vec[i] = 1.0
                        break
            out.append(vec)
        return out

    payload = m.evaluate(write=False, docs=docs, questions=questions, embed_fn=embed_fn)
    assert payload["schema_version"] == "rag_hybrid.v1"
    assert set(payload["comparison"]) == {"vector_only", "hybrid", "hybrid_syn", "hybrid_prf"}
    assert payload["chosen"] in payload["comparison"]
    for v in payload["comparison"].values():
        assert 0.0 <= v["recall@5"] <= 1.0 and 0.0 <= v["mrr"] <= 1.0
    assert payload["target"]["met"] in (True, False)
    assert len(payload["results"]) == len(questions)
    assert payload["chosen_by_type"]
