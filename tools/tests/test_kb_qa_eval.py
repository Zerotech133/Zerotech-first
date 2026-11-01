# -*- coding: utf-8 -*-
"""tools/tests/test_kb_qa_eval.py — 问答答案质量评估（wave16 支线 AU）单元测试。

覆盖（任务验收 ≥4）：
  1. 引用校验：unit_id 解析 / 页码区间 / 无法解析引用的标记；
  2. 支撑度分级：答案句子与引用文本的字符重叠 → 支持/弱支持/无支持，拒答句单列；
  3. 幻觉启发式：数字/英文专名不在引用文本 → 标记；在引用文本 → 不标记；过滤文件名类假 token；
  4. schema/报告：逐样本字段、汇总字段、MD 含问题清单；evaluate 对重复样本去重。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_kb_qa_eval.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))

import kb_qa_eval as ke  # noqa: E402


def _units():
    return [
        {"book": "研报A", "chapter": "第一章", "p1": 2, "p2": 5,
         "concept": "M9 覆铜板", "claim": "M9 级覆铜板提升 AI 服务器 PCB 价值量",
         "evidence": "224Gbps 超高速传输与 78 层量产，单颗 GPU 价值 600 美元"},
        {"book": "研报B", "chapter": "第二章", "p1": 10, "p2": 12,
         "concept": "钠电池", "claim": "钠电池应用场景拓展到储能",
         "evidence": "钠离子电池成本优势明显"},
    ]


def _id_map():
    return {
        "idA": {"source_unit_id": "idA", "book": "研报A", "page_start": 2,
                "page_end": 5, "concept": "M9 覆铜板"},
        "idB": {"source_unit_id": "idB", "book": "研报B", "page_start": 10,
                "page_end": 12, "concept": "钠电池"},
    }


def _sample(citations, answer="答案", sid="s1"):
    return {"id": sid, "question": "问题", "answer": answer, "citations": citations}


def _cit(uid="idA", page=3, page_end=4, text="", book="研报A"):
    return {"unit_id": uid, "book": book, "chapter": "第一章", "page": page,
            "page_end": page_end, "origin": "units", "text": text}


# ------------------------------------------------------------------ 1. 引用校验
def test_citation_existence_and_page():
    units, id_map = _units(), _id_map()
    # 合法引用：id 映射 + 页码在区间内
    r = ke.resolve_citation(_cit(), units, id_map)
    assert r["exists"] is True and r["method"] == "id_map" and r["page_ok"] is True
    # 页码越界
    r2 = ke.resolve_citation(_cit(page=99, page_end=99), units, id_map)
    assert r2["exists"] is True and r2["page_ok"] is False
    # 未知 id + 文本可匹配 → 可解析（text_match）
    r3 = ke.resolve_citation(_cit(uid="zzz", text="钠电池应用场景拓展到储能"),
                             units, id_map)
    assert r3["exists"] is True and str(r3["method"]).startswith("text_match")
    assert r3["id_known_in_map"] is False
    # 未知 id + 无文本/无法匹配 → 不存在
    r4 = ke.resolve_citation(_cit(uid="zzz", text="完全无关内容xyz"), units, id_map)
    assert r4["exists"] is False
    # 整样本 citation_valid：无引用 → False
    s = ke.eval_sample(_sample([]), units, id_map)
    assert s["citation_valid"] is False and "无引用" in s["issues"]


# ------------------------------------------------------------------ 2. 支撑度分级
def test_support_grading():
    units, id_map = _units(), _id_map()
    ref = "M9 级覆铜板提升 AI 服务器 PCB 价值量 224Gbps 超高速传输"
    assert ke.grade_support(0.9) == "支持"
    assert ke.grade_support(0.3) == "弱支持"
    assert ke.grade_support(0.05) == "无支持"
    # 高重叠句 → 支持；无关句 → 无支持
    s = ke.eval_sample(_sample([_cit()],
                               answer="M9 级覆铜板提升了 AI 服务器 PCB 价值量。"
                                      "今天天气很好适合出去散步。"),
                       units, id_map)
    assert s["support_counts"].get("支持", 0) >= 1
    assert s["support_counts"].get("无支持", 0) >= 1
    assert 0 < s["supported_ratio"] < 1
    # 拒答句单独归类，不计入可评分母
    s2 = ke.eval_sample(_sample([_cit()],
                                answer="知识库中没有足够依据回答该问题。"),
                        units, id_map)
    assert s2["support_counts"].get("拒答", 0) == 1
    assert s2["supported_ratio"] is None
    assert ke.is_refusal("知识库中没有足够依据回答该问题。") is True


# ------------------------------------------------------------------ 3. 幻觉启发式
def test_hallucination_heuristic():
    ref = "224Gbps 超高速传输与 78 层量产，单颗 GPU 价值 600 美元"
    # 数字与英文都在引用中 → 不标记
    assert ke.check_hallucination("224Gbps 传输，GPU 价值 600 美元", ref) == []
    # 数字不在引用 → number 标记
    flags = ke.check_hallucination("单颗 GPU 价值 9999 美元", ref)
    assert any(f["type"] == "number" and f["token"] == "9999" for f in flags)
    # 英文专名不在引用 → english_token 标记
    flags2 = ke.check_hallucination("采用 CoWoP 方案", ref)
    assert any(f["type"] == "english_token" and f["token"] == "CoWoP" for f in flags2)
    # 文件名/长数字类假 token 应被过滤（不产生 flag）
    flags3 = ke.check_hallucination("见 rp_AP202610071830186073 与 202610071830186073",
                                    ref)
    assert not any(f["token"].lower().startswith("rp_") for f in flags3)
    assert not any(f["type"] == "number" and len(f["token"]) > 6 for f in flags3)


# ------------------------------------------------------------------ 4. schema / 报告 / 去重
def test_schema_report_and_dedup():
    units, id_map = _units(), _id_map()
    main = [_sample([_cit()], answer="M9 级覆铜板提升 AI 服务器 PCB 价值量。", sid="s1")]
    expanded = [_sample([_cit()], answer="另一答案。", sid="s1"),  # 重复 id 应去重
                _sample([_cit("idB", 11)], answer="钠电池应用场景拓展。", sid="s2")]
    rep = ke.evaluate(main, expanded, units, id_map)
    assert rep["meta"]["schema_version"] == "kb_qa_eval.v1"
    assert rep["summary"]["n_samples"] == 2
    assert rep["summary"]["n_dedup_skipped"] == 1
    assert rep["summary"]["n_expanded"] == 1
    for key in ("citation_valid_rate", "citation_exists_rate", "avg_supported_ratio",
                "samples_with_hallucination_flags", "problems_count"):
        assert key in rep["summary"]
    row = rep["samples"][0]
    for key in ("id", "kind", "n_citations", "citations_exists", "citations_page_ok",
                "citation_exists_ratio", "citation_valid", "n_sentences",
                "support_counts", "supported_ratio", "hallucination_flags",
                "sentence_details", "issues"):
        assert key in row, f"逐样本缺少字段 {key}"
    md = ke.write_md(rep)
    for sec in ("汇总", "逐样本", "问题清单", "局限"):
        assert sec in md
    assert "幻觉" in md and "支撑" in md
    json.dumps(rep, ensure_ascii=False)   # 可序列化
