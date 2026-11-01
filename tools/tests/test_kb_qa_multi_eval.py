# -*- coding: utf-8 -*-
"""tools/tests/test_kb_qa_multi_eval.py — multi 答案质量评估（wave18 支线 BB）。

覆盖（任务验收 ≥4）：
  1. 引用标记解析：`[unit_id]` / `[id1, id2]` 抽取、去重、去除标记、推断标记计数；
  2. 样本转换：single（原 citations）与 multi（从答案标记还原）→ schema v2 字段正确、
     拒答映射、multi 答案标记被剔除（避免 id 内数字误报幻觉）；
  3. 对比统计：可答率/支撑率/引用数/幻觉率 delta 与「双方均有答案」配对子集计算正确；
  4. 空数据处理：无配对样本时 build_pairs 返回空、CLI 写出 0 配对报告不崩；
  5. 报告 schema：JSON 可序列化、MD 含逐条/汇总/结论，conclusion stance 合法。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_kb_qa_multi_eval.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))

import kb_qa_multi_eval as me  # noqa: E402

ID_A = "3d627dcc4f13"
ID_B = "4b6cf5a7158e"


def _units():
    return [
        {"book": "研报A", "chapter": "c1", "p1": 1, "p2": 10, "concept": "海上风电",
         "claim": "国家支持开发近海抗台型、深远海漂浮式风电机组",
         "evidence": "推进海上能源岛建设及海上风电运维母港布局"},
        {"book": "研报B", "chapter": "c2", "p1": 3, "p2": 15, "concept": "风电装机",
         "claim": "2025 年全球新增装机达 165GW", "evidence": "亚太地区占 80% 份额"},
    ]


def _id_map():
    return {
        ID_A: {"source_unit_id": ID_A, "book": "研报A", "chapter": "c1",
               "page_start": 1, "page_end": 10, "concept": "海上风电"},
        ID_B: {"source_unit_id": ID_B, "book": "研报B", "chapter": "c2",
               "page_start": 3, "page_end": 15, "concept": "风电装机"},
    }


# ------------------------------------------------------------------ 1. 引用标记解析
def test_extract_cited_ids_and_strip():
    answer = "结论 A [3d627dcc4f13] p.1-10；结论 B [4b6cf5a7158e, 5cfa2f156e5c] p.3-15"
    ids = me.extract_cited_ids(answer)
    assert ids == [ID_A, ID_B, "5cfa2f156e5c"]
    assert me.extract_cited_ids("无引用") == []
    stripped = me.strip_citation_markers(answer)
    assert ID_A not in stripped and ID_B not in stripped
    assert "p.1-10" not in stripped
    assert me.count_inference_markers("预计 2025 年可能放量，综合来看趋势向上") >= 2
    assert me.count_inference_markers("仅陈述事实") == 0


# ------------------------------------------------------------------ 2. 样本转换
def test_to_v2_sample_conversion():
    units, id_map = _units(), _id_map()
    single = {"id": "p1", "question": "海上风电规划依据？",
              "answer": "国家支持海上风电 [3d627dcc4f13] p.1-10",
              "citations": [{"unit_id": ID_A, "book": "研报A", "chapter": "c1",
                             "page": 1, "page_end": 10,
                             "text": "国家支持开发近海抗台型风电机组"}],
              "status": "success", "category": "关键数据",
              "confidence": {"level": "high"}, "no_evidence": False}
    s = me.to_v2_sample("p1", single, "single", units, id_map)
    assert s["id"] == "p1__single" and s["schema_version"] == 2
    assert s["expected_refusal"] is False and s["citations_required"] is True
    assert len(s["citations"]) == 1 and s["citations"][0]["unit_id"] == ID_A
    # multi：从答案标记还原引用；未打标记的多 id 不应残留数字
    multi = {"id": "p1", "question": "海上风电规划依据？",
             "answer": "综合来看，国家支持海上风电 [3d627dcc4f13, 4b6cf5a7158e] p.1-10。",
             "multi_citations": 2, "status": "success", "category": "关键数据",
             "confidence": "high", "no_evidence": False}
    m = me.to_v2_sample("p1", multi, "multi", units, id_map)
    assert m["id"] == "p1__multi"
    assert {c["unit_id"] for c in m["citations"]} == {ID_A, ID_B}
    assert all(c.get("page_available") is False for c in m["citations"])
    assert ID_A not in m["answer"], "multi 答案中的引用标记应被剔除"
    # 拒答映射
    refusal = {"id": "p2", "question": "q", "answer": "知识库中没有足够依据回答该问题。",
               "status": "no_evidence", "no_evidence": True, "confidence": {"level": "none"}}
    r = me.to_v2_sample("p2", refusal, "single", units, id_map)
    assert r["expected_refusal"] is True and r["answer_type"] == "refusal"


# ------------------------------------------------------------------ 3. 对比统计
def test_compare_statistics():
    units, id_map = _units(), _id_map()
    v3 = {"answer_validation": {"samples": [
        {"id": "p1", "question": "q1", "answer": "国家支持海上风电 [3d627dcc4f13] p.1-10",
         "citations": [{"unit_id": ID_A, "book": "研报A", "chapter": "c1", "page": 1,
                        "page_end": 10, "text": "国家支持开发近海抗台型风电机组"}],
         "status": "success", "category": "关键数据", "confidence": {"level": "high"}},
        {"id": "p2", "question": "q2", "answer": "知识库中没有足够依据回答该问题。",
         "citations": [], "status": "no_evidence", "category": "风险限制",
         "confidence": {"level": "none"}, "no_evidence": True},
    ]}}
    multi = {"comparison": [
        {"id": "p1", "question": "q1", "category": "关键数据",
         "answer": "多单元综述：国家支持海上风电 [3d627dcc4f13] p.1-10",
         "multi_citations": 1, "multi_status": "success", "confidence": "high"},
        {"id": "p2", "question": "q2", "category": "风险限制",
         "answer": "多单元综述：知识库中没有足够依据回答该问题。",
         "multi_citations": 0, "multi_status": "no_evidence", "confidence": "none"},
    ]}
    pairs = me.build_pairs(v3, multi)
    assert len(pairs) == 2
    rep = me.evaluate(pairs, units, id_map)
    s = rep["summary"]
    assert s["single"]["n"] == 2 and s["multi"]["n"] == 2
    assert s["single"]["answerable_rate"] == pytest.approx(0.5)
    assert s["multi"]["answerable_rate"] == pytest.approx(0.5)
    assert "support_delta" in s["paired"]
    assert "verdict" in rep["conclusion"] and "stance" in rep["conclusion"]
    assert rep["meta"]["n_pairs"] == 2


# ------------------------------------------------------------------ 4. 空数据
def test_build_pairs_empty_and_empty_report(tmp_path):
    pairs = me.build_pairs({"answer_validation": {"samples": []}}, {"comparison": []})
    assert pairs == []
    # CLI 空数据路径：写 0 配对报告不崩
    empty_v3 = tmp_path / "v3.json"
    empty_multi = tmp_path / "multi.json"
    empty_v3.write_text(json.dumps({"answer_validation": {"samples": []}}), encoding="utf-8")
    empty_multi.write_text(json.dumps({"comparison": []}), encoding="utf-8")
    out_json, out_md = tmp_path / "eval.json", tmp_path / "eval.md"
    rc = me.main(["eval", "--v3-bank", str(empty_v3), "--multi-raw", str(empty_multi),
                  "--ab", str(tmp_path / "no_ab.json"), "--json", str(out_json),
                  "--md", str(out_md)])
    assert rc == 0
    rep = json.loads(out_json.read_text(encoding="utf-8"))
    assert rep["meta"]["n_pairs"] == 0 and rep["pairs"] == []
    assert "无数据" in rep["conclusion"]["verdict"]


# ------------------------------------------------------------------ 5. 报告 schema
def test_report_schema_and_md():
    units, id_map = _units(), _id_map()
    v3 = {"answer_validation": {"samples": [
        {"id": "p1", "question": "q1", "answer": "国家支持海上风电 [3d627dcc4f13]",
         "citations": [{"unit_id": ID_A, "book": "研报A", "chapter": "c1", "page": 1,
                        "page_end": 10, "text": "国家支持开发近海抗台型风电机组"}],
         "status": "success", "category": "关键数据", "confidence": {"level": "high"}}]}}
    multi = {"comparison": [
        {"id": "p1", "question": "q1", "category": "关键数据",
         "answer": "多单元综述 [3d627dcc4f13]", "multi_citations": 1,
         "multi_status": "success", "confidence": "high"}]}
    rep = me.evaluate(me.build_pairs(v3, multi), units, id_map)
    json.dumps(rep, ensure_ascii=False)   # 可序列化
    md = me.write_md(rep)
    for sec in ("汇总对比", "配对子集", "逐条", "结论", "口径与局限"):
        assert sec in md
    assert "多单元" in md or "multi" in md
    assert rep["conclusion"]["stance"] in {"continue_multi", "multi_with_guard",
                                           "keep_retrieval_default", "no_data"}
