# -*- coding: utf-8 -*-
"""tools/tests/test_search_v2_eval_ext.py — 检索 v2 评测扩展（wave9 支线 X）单元测试。

覆盖（任务验收 ≥3）：
  1. 查询集规模/语法覆盖：30–50 条、6 类语法齐全、id/查询去重；
  2. 标注完整性：每条有 expected_concepts 且能在数据中找到 ≥1 个期望单元（零期望=标注错误）；
  3. 相关判定与指标纯函数：三字段/仅 concept 口径、precision@k 分母固定、覆盖率；
  4. 显著性数学：配对 t（正态近似）、McNemar 精确检验；
  5. 评测 schema 与报告/文档追加：JSON 可序列化、md 含汇总表、doc 标记块幂等。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_search_v2_eval_ext.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))

import eval_search_v2_ext as ev  # noqa: E402
import search_v2 as sv  # noqa: E402

UNIT_JSON = ROOT / "portal" / "static" / "kb_units_all.json"


def _units() -> list[dict]:
    if not UNIT_JSON.exists():
        pytest.skip(f"数据文件不存在: {UNIT_JSON}")
    return sv.load_units(UNIT_JSON)


# ------------------------------------------------------------------ 1. 查询集规模
def test_query_set_scale_and_types():
    qs = ev.QUERIES
    assert 30 <= len(qs) <= 50, f"查询集 {len(qs)} 条，要求 30–50"
    ids = [q["id"] for q in qs]
    assert len(set(ids)) == len(ids), "查询 id 重复"
    texts = [q["q"] for q in qs]
    assert len(set(texts)) == len(texts), "查询文本重复"
    types = {q["type"] for q in qs}
    assert set(ev.QUERY_TYPES) <= types, f"语法类型覆盖不全: {types}"
    # 各类语法至少 2 条
    for t in ev.QUERY_TYPES:
        assert sum(1 for q in qs if q["type"] == t) >= 2, f"类型 {t} 数量不足"


# ------------------------------------------------------------------ 2. 标注完整性
def test_annotation_completeness():
    units = _units()
    zero = []
    for q in ev.QUERIES:
        assert q["expected_concepts"], f"{q['id']} 缺少 expected_concepts"
        assert all(isinstance(k, str) and k.strip() for k in q["expected_concepts"])
        n = len(ev.expected_unit_ids(units, q["expected_concepts"]))
        if n == 0:
            zero.append((q["id"], q["query"], q["expected_concepts"]))
    assert not zero, f"存在零期望单元的查询（标注错误）: {zero}"
    # 期望单元数不宜过多（否则“相关”失去区分度）：全集中位 < 60%
    counts = [len(ev.expected_unit_ids(units, q["expected_concepts"])) for q in ev.QUERIES]
    assert sorted(counts)[len(counts) // 2] < len(units) * 0.6


# ------------------------------------------------------------------ 3. 相关判定与指标
def test_relevance_and_metrics_pure():
    u = {"concept": "量学理论", "claim": "量柱选股的三维互动", "evidence": "量线选价"}
    assert ev.is_relevant(u, ["量柱"]) is True                 # 三字段口径命中 claim
    assert ev.is_relevant(u, ["量柱"], fields=("concept",)) is False  # 严格口径
    assert ev.is_relevant(u, ["量学理论"], fields=("concept",)) is True
    # precision@k 分母固定为 k（结果不足 k 条时仍除以 k）
    top = [{"concept": "量柱完全分类"}, {"concept": "无关"}]
    assert ev.precision_at_k(top, ["量柱"], k=5) == pytest.approx(0.2)
    assert ev.precision_at_k(top, ["量柱"], k=2) == pytest.approx(0.5)
    with pytest.raises(ValueError):
        ev.precision_at_k(top, ["量柱"], k=0)
    # 覆盖率：标注键在结果文本中的命中比例
    top2 = [{"concept": "量柱完全分类", "claim": "量线定义", "evidence": ""}]
    assert ev.coverage_at_k(top2, ["量柱", "量线"]) == pytest.approx(1.0)
    assert ev.coverage_at_k(top2, ["量柱", "光伏"]) == pytest.approx(0.5)
    assert ev.coverage_at_k(top2, []) == 0.0


# ------------------------------------------------------------------ 4. 显著性数学
def test_significance_math():
    r = ev.paired_t_p([0.2, 0.2, 0.2, 0.2])
    assert r["mean"] == pytest.approx(0.2) and r["p_two_sided"] == 0.0
    r2 = ev.paired_t_p([0.2, -0.2, 0.2, -0.2])
    assert r2["mean"] == pytest.approx(0.0) and r2["p_two_sided"] == pytest.approx(1.0, abs=1e-9)
    assert ev.paired_t_p([0.1])["p_two_sided"] is None
    assert ev.mcnemar_exact(0, 0)["p_two_sided"] == 1.0
    assert ev.mcnemar_exact(4, 0)["p_two_sided"] == pytest.approx(0.125)
    assert ev.mcnemar_exact(5, 0)["p_two_sided"] == pytest.approx(0.0625)
    assert ev.mcnemar_exact(3, 3)["p_two_sided"] == pytest.approx(1.0)


# ------------------------------------------------------------------ 5. schema / 报告 / 文档追加
def test_eval_schema_report_and_doc_append(tmp_path):
    units = [
        {"book": "书A", "chapter": "c1", "concept": "光伏行业供给出清",
         "claim": "光伏组件价格触底", "evidence": "硅料下跌"},
        {"book": "书A", "chapter": "c1", "concept": "钠电池应用场景",
         "claim": "钠电池储能应用拓展", "evidence": "钠离子电池"},
        {"book": "书B", "chapter": "c2", "concept": "白酒价格带分化",
         "claim": "白酒动销", "evidence": "消费复苏"},
    ]
    engine = sv.SearchV2(units, synonyms=sv.SynonymTable([["光伏", "太阳能"]]))
    queries = [
        {"id": "T1", "q": "太阳能", "type": "同义词", "expected_concepts": ["光伏"]},
        {"id": "T2", "q": "钠电池|白酒", "type": "OR",
         "expected_concepts": ["钠电池", "白酒"]},
    ]
    rep = ev.evaluate_ext(engine, units, queries, topk=2)
    assert rep["meta"]["schema_version"] == "search_v2_eval_ext.v1"
    assert rep["meta"]["n_queries"] == 2 and rep["meta"]["topk"] == 2
    assert set(rep["aggregate"]) >= {"v1", "v2", "delta"}
    assert set(rep["significance"]) >= {"precision_at_k_paired_t", "hit_rate_mcnemar"}
    for r in rep["queries"]:
        assert r["n_expected_units"] >= 1
        for k in ("precision_at_k", "coverage", "hit"):
            assert 0.0 <= r["v2"][k] <= 1.0
    # JSON 可序列化
    txt = json.dumps(rep, ensure_ascii=False)
    assert len(txt) > 100
    # md 报告包含汇总/显著性/逐查询
    md = ev.write_md(rep)
    assert "precision@5" in md and "显著" in md and "逐查询" in md
    # doc 追加幂等：两次追加只保留一个标记块
    doc = tmp_path / "plan.md"
    doc.write_text("# 设计文档\n\n## 6. 小评测\n\n旧内容\n", encoding="utf-8")
    ev.append_doc(md, rep, doc)
    ev.append_doc(md, rep, doc)
    text = doc.read_text(encoding="utf-8")
    assert text.count(ev.DOC_BEGIN) == 1 and text.count(ev.DOC_END) == 1
    assert "评测扩展" in text and "v2" in text
    # 原有内容保留
    assert "旧内容" in text and text.startswith("# 设计文档")
