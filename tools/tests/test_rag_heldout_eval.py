# -*- coding: utf-8 -*-
"""tools/tests/test_rag_heldout_eval.py · W12 真实 held-out RAG 复评（离线/mock）单元测试。

覆盖（≥4）：
  1. `parse_source`：`xxx.md L131` 解析、书页格式（《…》 p6-12）返回 (None,None)；
  2. `resolve_gold`：笔记题按「源文件+行号」精确命中知识单元、概念题按卡 concept 精确/源文件行/概念文本匹配、
     答案 bigram 兜底、无法解析返回空；
  3. `build_questions`：50 题结构（概念 25 = A 10 + C_reading 15 + 笔记 25）、确定性、gold 非空、
     笔记题由笔记原文构造阅读理解题；
  4. `evaluate`（mock embed/chat，不联网）：指标聚合 recall@5/10/MRR/引用率/一致性 schema 正确、
     分题型齐全、与 W6 合成基线的对比字段存在；
  5. 离线约束：模块源码不直接 urllib/网络调用，`--no-gen` 路径不调用 chat；
  6. `render_markdown` 关键小节存在。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_rag_heldout_eval.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
KB_CORPUS = ROOT / "tools" / "kb_corpus"
sys.path.insert(0, str(KB_CORPUS))

import rag_heldout_eval as r  # noqa: E402

MODULE = KB_CORPUS / "rag_heldout_eval.py"


# ------------------------------------------------------------------ 1. source 解析
def test_parse_source():
    assert r.parse_source("20261103_乔木缠论ROEE.md L131") == ("20261103_乔木缠论ROEE.md", 131)
    assert r.parse_source("xxx.md L7") == ("xxx.md", 7)
    assert r.parse_source("《rp_AP202609301830015406》 供应链风险管理。 p6-12") == (None, None)
    assert r.parse_source("") == (None, None)
    assert r.parse_source("no line here") == (None, None)


# ------------------------------------------------------------------ 2. gold 解析
def _doc(doc_id, kind, text, *, file=None, line=None, concept=None):
    return {"doc_id": doc_id, "kind": kind, "text": text, "source": file or "",
            "file": file or "", "line": line, "concept": concept or "",
            "project": "", "unit_id": doc_id[2:] if kind == "unit" else None}


def _item(category, concept, source, user="", answer=""):
    return {"meta": {"category": category, "concept": concept, "source": source},
            "_category": category, "_user": user, "_answer": answer, "messages": []}


def test_resolve_gold_signals():
    docs = [
        _doc("U:u1", "unit", "缠论买点判断依据", file="a.md", line=10),
        _doc("U:u2", "unit", "涨停基因的核心是倍量柱", file="b.md", line=5),
        _doc("C:000", "card", "频谱资源与许可 可靠频谱接入是关键资源", concept="频谱资源与许可"),
        _doc("C:001", "card", "其他内容", concept="其他"),
    ]
    gi = r.build_gold_index(docs)
    # 笔记题：源文件+行号精确命中单元
    gold, sig = r.resolve_gold(_item("C_heldout_note", "x", "a.md L10"), gi, docs)
    assert gold == ["U:u1"] and sig == "note_source_line_exact"
    # 笔记题：行号缺失/不命中 → 同文件最近行
    gold, sig = r.resolve_gold(_item("C_heldout_note", "x", "b.md L9"), gi, docs)
    assert gold == ["U:u2"] and sig == "note_source_line_nearest"
    # 概念题：卡 concept 精确
    gold, sig = r.resolve_gold(_item("A_concept_qa", "频谱资源与许可", "《书》 p1-2"), gi, docs)
    assert "C:000" in gold and sig == "card_concept_exact"
    # 概念题：源文件+行号
    gold, sig = r.resolve_gold(_item("A_concept_qa", "某概念", "a.md L10"), gi, docs)
    assert gold == ["U:u1"] and sig == "concept_source_line"
    # 概念题：概念文本匹配
    gold, sig = r.resolve_gold(_item("A_concept_qa", "倍量柱", "《书》 p1-2"), gi, docs)
    assert gold == ["U:u2"] and sig == "concept_text_match"
    # 兜底：与答案 bigram 重叠
    gold, sig = r.resolve_gold(_item("A_concept_qa", "未知概念", "《书》 p9", answer="频谱资源与许可 可靠频谱接入是关键资源"), gi, docs)
    assert gold and sig == "concept_answer_overlap"


# ------------------------------------------------------------------ 3. 题集构造
def test_build_questions_structure_and_determinism():
    docs = [
        _doc("U:u1", "unit", "缠论买点判断依据", file="a.md", line=10),
        _doc("U:u2", "unit", "倍量柱判断", file="a.md", line=20),
        _doc("C:000", "card", "频谱资源与许可 可靠频谱接入", concept="频谱资源与许可"),
    ]
    items = (
        [_item("A_concept_qa", "频谱资源与许可", "《书》 p1", user="什么是「频谱资源与许可」？",
               answer="可靠频谱接入是关键资源") for i in range(20)]
        + [_item("C_reading_comprehension", f"主题{i}", f"a.md L{10 + (i % 2) * 10}",
                 user=f"文中如何判断主题{i}？", answer=f"原文{i}") for i in range(35)]
        + [_item("C_heldout_note", f"笔记{i}", f"a.md L{10 + (i % 2) * 10}", answer=f"第{i}个，做独立验证。先补流程。")
           for i in range(50)]
    )
    qs1 = r.build_questions(items, docs)
    qs2 = r.build_questions(items, docs)
    assert len(qs1) == 50
    types = [q["type"] for q in qs1]
    assert types.count("concept_real") == 10
    assert types.count("reading_comprehension") == 15
    assert types.count("note_reading") == 25
    assert [q["qid"] for q in qs1] == [q["qid"] for q in qs2]
    assert [q["question"] for q in qs1] == [q["question"] for q in qs2]
    for q in qs1:
        assert q["gold_doc_ids"], q["qid"]
        assert q["question"].strip()
    # 笔记题包含原文与阅读理解指令
    note_q = next(q for q in qs1 if q["type"] == "note_reading")
    assert "笔记原文" in note_q["question"] and "核心观点" in note_q["question"]


# ------------------------------------------------------------------ 4. evaluate（mock，不联网）
class _MockEmbed:
    """二值向量：包含 alpha→[1,0,0]；包含 beta→[0,1,0]；其他→[0,0,1]。"""

    def __call__(self, texts):
        out = []
        for t in texts:
            t = str(t)
            out.append([1.0 if "alpha" in t else 0.0,
                        1.0 if "beta" in t else 0.0,
                        1.0 if ("gamma" in t or not ("alpha" in t or "beta" in t)) else 0.0])
        return out


def test_evaluate_with_mock_embed_chat():
    docs = [
        _doc("U:u1", "unit", "alpha 买点依据", file="a.md", line=10),
        _doc("U:u2", "unit", "beta 成交量", file="a.md", line=20),
        _doc("C:000", "card", "alpha 概念卡", concept="alpha"),
        _doc("C:001", "card", "gamma 其他", concept="gamma"),
    ]
    items = (
        [_item("A_concept_qa", "alpha", "《书》 p1", user="什么是「alpha」？", answer="alpha 解释")
         for _ in range(10)]
        + [_item("C_reading_comprehension", "alpha", "a.md L10", user="文中 alpha 如何判断？", answer="alpha 原文")
           for _ in range(15)]
        + [_item("C_heldout_note", "alpha", "a.md L10", answer="alpha 笔记原文")
           for _ in range(25)]
    )
    calls = {"chat": 0}

    def chat_fn(prompt: str) -> str:
        calls["chat"] += 1
        return "依据 [1] 回答。alpha"  # 含引用标记

    payload = r.evaluate(docs=docs, items=items, gen_sample=3,
                         embed_fn=_MockEmbed(), chat_fn=chat_fn)
    agg = payload["aggregate"]
    assert agg["questions_total"] == 50 and agg["evaluated"] == 50
    assert agg["recall@5"] == 1.0 and agg["recall@10"] == 1.0
    # 同分并列：U:u1 与 C:000 向量相同，卡类 gold(C:000) 排名 2 → MRR=0.75（25 题 0.5 + 25 题 1.0）
    assert agg["mrr"] == pytest.approx(0.75, abs=1e-6)
    assert agg["generation_evaluated"] == 3 and calls["chat"] == 3
    assert agg["citation_rate"] == 1.0
    assert agg["unresolved_gold"] == 0
    assert set(agg["by_type"]) == {"concept_real", "reading_comprehension", "note_reading"}
    assert payload["comparison"]["w6_synthetic"]["recall@5"] == 0.84
    assert "delta" in payload["comparison"]
    for key in ("method", "sources", "targets", "difficulty_notes", "results", "limitations"):
        assert key in payload, key
    md = r.render_markdown(payload)
    assert "核心指标" in md and "难度差异" in md and "结论" in md


def test_no_gen_skips_chat_and_offline_source():
    docs = [_doc("U:u1", "unit", "alpha 单元", file="a.md", line=1),
            _doc("C:000", "card", "alpha 卡", concept="alpha")]
    items = _items_minimal()
    payload = r.evaluate(docs=docs, items=items, gen_sample=0,
                         embed_fn=_MockEmbed(), chat_fn=lambda p: (_ for _ in ()).throw(AssertionError("不应调用")),
                         questions=r.build_questions(items, docs))
    assert payload["aggregate"]["generation_evaluated"] == 0
    # 模块源码不直接发起网络调用（网络只在 w6 复用函数内部）
    src = MODULE.read_text(encoding="utf-8")
    assert "urllib" not in src
    assert "http://127.0.0.1:11434" not in src


def _items_minimal():
    return (
        [_item("A_concept_qa", "alpha", "《书》 p1", user="什么是 alpha？", answer="alpha") for _ in range(10)]
        + [_item("C_reading_comprehension", "alpha", "a.md L1", user="文中 alpha？", answer="alpha") for _ in range(15)]
        + [_item("C_heldout_note", "alpha", "a.md L1", answer="alpha 笔记") for _ in range(25)]
    )
