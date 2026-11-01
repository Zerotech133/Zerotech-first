# -*- coding: utf-8 -*-
"""tools/tests/test_search_v2_eval_ext2.py — 检索 v2 评测第二阶段（wave10 支线 AA）。

覆盖（任务验收 ≥3）：
  1. 查询集规模/来源/类型：60–100 条、id 唯一、8 类语法/长度齐全；
  2. 分级标注完整性：每条有 grade-2 标注、期望单元非空、grade 键合法；
  3. NDCG 计算正确性：分级相关、DCG 指数增益公式、理想排序 NDCG=1、乱序 <1；
  4. bootstrap CI 与配对统计：全正差异 → CI 不含 0；空输入安全；
  5. 评测 schema / 报告 / 文档追加：JSON 可序列化、MD 含 NDCG、标记块幂等。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_search_v2_eval_ext2.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))

import eval_search_v2_ext2 as e2  # noqa: E402
import search_v2 as sv  # noqa: E402

UNIT_JSON = ROOT / "portal" / "static" / "kb_units_all.json"


def _units() -> list[dict]:
    if not UNIT_JSON.exists():
        pytest.skip(f"数据文件不存在: {UNIT_JSON}")
    return sv.load_units(UNIT_JSON)


# ------------------------------------------------------------------ 1. 查询集规模
def test_query_scale_sources_types():
    qs = e2.all_queries()
    assert 60 <= len(qs) <= 100, f"查询集 {len(qs)} 条，要求 60–100"
    ids = [q["id"] for q in qs]
    assert len(set(ids)) == len(ids), "查询 id 重复"
    src = {q["source"] for q in qs}
    assert src == {"wave9", "wave10"}
    assert sum(1 for q in qs if q["source"] == "wave9") == len(e2.ev.QUERIES)
    assert sum(1 for q in qs if q["source"] == "wave10") == len(e2.EXTRA_QUERIES)
    types = {q["type"] for q in qs}
    assert set(e2.QUERY_TYPES) <= types, f"类型覆盖不全: {types}"
    # 8 类各至少 3 条（主题除外，要求 >10）
    for t in e2.QUERY_TYPES:
        n = sum(1 for q in qs if q["type"] == t)
        assert n >= (10 if t == "主题" else 3), f"类型 {t} 仅 {n} 条"


# ------------------------------------------------------------------ 2. 分级标注完整性
def test_graded_annotation_completeness():
    units = _units()
    for q in e2.all_queries():
        graded = q["graded"]
        assert isinstance(graded, dict) and set(graded) <= {"1", "2"}
        g2 = graded.get("2") or []
        assert g2, f"{q['id']} 缺少 grade-2 标注"
        for k in g2 + (graded.get("1") or []):
            assert isinstance(k, str) and k.strip(), f"{q['id']} 标注含空关键词"
        grades = e2.expected_grades(units, graded)
        assert grades, f"{q['id']} 在 498 单元中找不到任何期望单元（标注错误）"
        assert max(grades) == 2, f"{q['id']} 期望单元中无 grade-2 单元"
        # 期望单元不应覆盖全库（否则失去区分度）
        assert len(grades) < len(units) * 0.8


# ------------------------------------------------------------------ 3. NDCG 正确性
def test_ndcg_correctness():
    graded = {"2": ["高度相关主题"], "1": ["部分相关主题"]}
    units = [
        {"concept": "高度相关主题", "claim": "x", "evidence": ""},
        {"concept": "部分相关主题", "claim": "x", "evidence": ""},
        {"concept": "无关主题", "claim": "x", "evidence": ""},
    ]
    assert e2.unit_grade(units[0], graded) == 2
    assert e2.unit_grade(units[1], graded) == 1
    assert e2.unit_grade(units[2], graded) == 0
    # DCG：3 + (2-1)/log2(3)
    assert e2.dcg_at_k([2, 1]) == pytest.approx(3.0 + 1.0 / math.log2(3), abs=1e-9)
    assert e2.dcg_at_k([2, 1, 0]) == pytest.approx(3.0 + 1.0 / math.log2(3), abs=1e-9)
    # 理想排序 → NDCG=1
    ideal_top = [units[0], units[1]]
    assert e2.ndcg_at_k(ideal_top, graded, units) == pytest.approx(1.0, abs=1e-9)
    # 乱序（部分相关在前、高度相关在后）→ NDCG < 1
    worse_top = [units[1], units[0]]
    nd = e2.ndcg_at_k(worse_top, graded, units)
    assert 0 < nd < 1
    # 完全无关 → 0
    assert e2.ndcg_at_k([units[2]], graded, units) == 0.0
    # 期望单元分级降序
    assert e2.expected_grades(units, graded) == [2, 1]
    # P@5/命中：相关=等级>0；分母固定 k
    p, rel = e2.precision_hit([units[1], units[2]], graded, k=5)
    assert p == pytest.approx(1 / 5) and rel == [1, 0]


# ------------------------------------------------------------------ 4. bootstrap / 统计
def test_bootstrap_ci_and_stats():
    pos = e2.bootstrap_ci([0.2, 0.3, 0.25, 0.35, 0.1])
    assert pos["ci_low"] > 0 and pos["significant"] is True
    assert pos["ci_low"] <= pos["mean"] <= pos["ci_high"]
    mixed = e2.bootstrap_ci([0.2, -0.2, 0.1, -0.1])
    assert mixed["ci_low"] < 0 < mixed["ci_high"] and mixed["significant"] is False
    assert e2.bootstrap_ci([])["ci_low"] is None
    # 同一输入固定 seed 可复现
    assert e2.bootstrap_ci([0.1, 0.2, 0.0]) == e2.bootstrap_ci([0.1, 0.2, 0.0])


# ------------------------------------------------------------------ 5. schema / 报告 / 文档
def test_eval_schema_report_and_doc(tmp_path):
    units = [
        {"book": "书A", "chapter": "c1", "concept": "光伏行业供给出清",
         "claim": "光伏组件价格触底", "evidence": "硅料下跌"},
        {"book": "书A", "chapter": "c1", "concept": "太阳能装机", "claim": "光伏新增装机",
         "evidence": ""},
        {"book": "书B", "chapter": "c2", "concept": "白酒价格带分化",
         "claim": "白酒动销", "evidence": "消费复苏"},
    ]
    engine = sv.SearchV2(units, synonyms=sv.SynonymTable([["光伏", "太阳能"]]))
    queries = [
        {"id": "T1", "q": "太阳能", "type": "同义词",
         "graded": {"2": ["光伏"], "1": ["太阳能"]}, "source": "wave10"},
        {"id": "T2", "q": "白酒|光伏", "type": "OR",
         "graded": {"2": ["白酒", "光伏"], "1": ["消费"]}, "source": "wave10"},
        {"id": "T3", "q": "白酒 动销", "type": "AND",
         "graded": {"2": ["白酒"], "1": ["动销"]}, "source": "wave10"},
    ]
    rep = e2.evaluate_ext2(engine, units, queries, topk=2)
    assert rep["meta"]["schema_version"] == "search_v2_eval_ext2.v1"
    assert rep["meta"]["n_queries"] == 3
    assert set(rep["aggregate"]) >= {"v1", "v2", "delta"}
    assert set(rep["aggregate"]["v2"]) >= {"precision_at_k", "coverage", "hit_rate", "ndcg_at_k"}
    assert set(rep["significance"]) >= {"precision_at_k_paired_t", "ndcg_at_k_paired_t",
                                        "precision_at_k_bootstrap", "ndcg_at_k_bootstrap",
                                        "hit_rate_mcnemar"}
    for r in rep["queries"]:
        assert r["n_expected_units"] >= 1
        for k in ("precision_at_k", "coverage", "ndcg_at_k"):
            assert 0.0 <= r["v2"][k] <= 1.0
    json.dumps(rep, ensure_ascii=False)  # 可序列化
    md = e2.write_md(rep)
    assert "NDCG@5" in md and "Bootstrap" in md and "逐查询" in md
    # 文档追加幂等
    doc = tmp_path / "plan.md"
    doc.write_text("# 设计\n\n## 5) 小评测\n\n旧内容\n", encoding="utf-8")
    e2.append_doc(rep, doc)
    e2.append_doc(rep, doc)
    txt = doc.read_text(encoding="utf-8")
    assert txt.count(e2.DOC_BEGIN) == 1 and txt.count(e2.DOC_END) == 1
    assert "第二阶段" in txt and "旧内容" in txt and txt.startswith("# 设计")
