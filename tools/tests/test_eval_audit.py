# -*- coding: utf-8 -*-
"""tools/tests/test_eval_audit.py — 两阶段偏差审计（wave11 支线 AF）单元测试。

覆盖（任务验收 ≥3）：
  1. 重叠子集匹配与复现：wave9 41 条在 wave10 中 id 完全重合、标注一致、指标可复现；
  2. 偏差分解数学：base(g2) → overlap(grade>0) → full 的标注/查询集效应计算正确；
  3. schema/报告：JSON 可序列化、MD 含各章节与结论、允许/禁止表述齐全；
  4. 显著性工具：bootstrap CI / 配对 t（复用 wave9/10 模块）行为正确。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_eval_audit.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))

import eval_audit as ea  # noqa: E402


def _synthetic():
    """2 条重叠查询 + 1 条新增查询的最小两阶段报告。"""
    def w10_row(qid, v1_grades, v2_grades, typ="主题", g2=("目标",), g1=("相关",)):
        def pack(grades, ndcg):
            return {"grades": list(grades), "coverage": 1.0, "hit": 1,
                    "ndcg_at_k": ndcg, "precision_at_k": 0.0}
        return {"id": qid, "query": qid, "type": typ,
                "graded": {"2": list(g2), "1": list(g1)},
                "v1": pack(v1_grades, 0.5), "v2": pack(v2_grades, 0.9)}
    w10 = {
        "meta": {"n_queries": 3, "topk": 5},
        "queries": [
            w10_row("Q1", [2, 2, 0, 0, 0], [2, 2, 2, 0, 0]),
            w10_row("Q2", [2, 0, 0, 0, 0], [2, 2, 0, 0, 0]),
            w10_row("E1", [0, 0, 0, 0, 0], [1, 1, 0, 0, 0], typ="AND"),
        ],
    }
    w9 = {
        "meta": {"n_queries": 2},
        "queries": [
            {"id": "Q1", "query": "Q1", "type": "主题", "expected_concepts": ["目标"],
             "v1": {"precision_at_k": 0.4}, "v2": {"precision_at_k": 0.6}},
            {"id": "Q2", "query": "Q2", "type": "主题", "expected_concepts": ["目标"],
             "v1": {"precision_at_k": 0.2}, "v2": {"precision_at_k": 0.4}},
        ],
        "aggregate": {"v1": {"precision_at_k": 0.3}, "v2": {"precision_at_k": 0.5}},
        "significance": {"precision_at_k_paired_t": {"t": 2.0, "p_two_sided": 0.05}},
        "sensitivity": None,
    }
    return w9, w10


# ------------------------------------------------------------------ 1. 重叠与复现
def test_overlap_and_reproduction_real():
    if not (ea.W9_JSON.exists() and ea.W10_JSON.exists()):
        pytest.skip("两阶段评测 JSON 不存在，跳过真实数据校验")
    w9 = ea.load_report(ea.W9_JSON)
    w10 = ea.load_report(ea.W10_JSON)
    ids = ea.overlap_ids(w9, w10)
    assert len(ids) == 41
    assert len(ids) == len(set(ids))
    rep = ea.compose(w9, w10)
    ov = rep["overlap_validation"]
    assert ov["annotation_identical"] == 41
    assert ov["annotation_changed"] == []
    assert ov["reproduced_exactly"] is True
    assert max(ov["reproduction_max_abs_diff"].values()) < 1e-6


# ------------------------------------------------------------------ 2. 分解数学
def test_decomposition_math_synthetic():
    w9, w10 = _synthetic()
    rep = ea.compose(w9, w10)
    st = rep["stages"]
    # base：仅 g2（Q1: v1 0.4/v2 0.6；Q2: v1 0.2/v2 0.4）→ Δ=0.2
    assert st["overlap_41_g2"]["v1"]["precision_at_k"] == pytest.approx(0.3)
    assert st["overlap_41_g2"]["v2"]["precision_at_k"] == pytest.approx(0.5)
    # overlap graded（grade>0）：Q1 v1 0.4/v2 0.6；Q2 v1 0.2/v2 0.4 → 相同 Δ=0.2
    assert st["overlap_41_graded"]["v2"]["precision_at_k"] == pytest.approx(0.5)
    # full 89（此处 3 条）：新增 E1 v1 0/v2 0.4（grade 1 计相关）→ v1=(0.4+0.2+0)/3,
    # v2=(0.6+0.4+0.4)/3 → Δ=0.1333
    f = st["full_89_graded"]
    assert f["v1"]["precision_at_k"] == pytest.approx(0.2)
    assert f["v2"]["precision_at_k"] == pytest.approx(0.4667)
    d = rep["delta_decomposition"]
    assert d["stages"]["wave9_reported"] == pytest.approx(0.2)
    assert d["stages"]["wave9_reproduced_g2_41"] == pytest.approx(0.2)
    assert d["stages"]["wave10_full_89"] == pytest.approx(0.2667, abs=1e-3)
    assert d["effects"]["annotation_effect_on_delta"] == pytest.approx(0.0, abs=1e-6)
    assert d["effects"]["query_selection_effect_on_delta"] == pytest.approx(0.0667, abs=1e-3)
    # 组合构成：重叠 2 条、新增 1 条（AND）
    assert rep["composition"]["new_types"].get("AND") == 1
    assert rep["composition"]["overlap_types"].get("主题") == 2


# ------------------------------------------------------------------ 3. schema / 报告
def test_schema_and_md():
    w9, w10 = _synthetic()
    rep = ea.compose(w9, w10)
    assert rep["meta"]["schema_version"] == "eval_audit.v1"
    assert rep["meta"]["n_overlap"] == 2 and rep["meta"]["n_wave10"] == 3
    assert set(rep) >= {"meta", "overlap_validation", "stages", "delta_decomposition",
                        "significance", "composition", "conclusion"}
    json.dumps(rep, ensure_ascii=False)  # 可序列化
    c = rep["conclusion"]
    assert c["trustworthy_stage"]
    assert c["statement_norms"]["allowed"] and c["statement_norms"]["forbidden"]
    md = ea.write_md(rep)
    for sec in ("重叠子集校验", "偏差分解", "显著性变化", "结论", "v1/v2 表述规范"):
        assert sec in md
    assert "允许" in md and "禁止" in md


# ------------------------------------------------------------------ 4. 显著性工具
def test_significance_helpers_via_audit_inputs():
    w9, w10 = _synthetic()
    rep = ea.compose(w9, w10)
    sig = rep["significance"]
    assert sig["full_89_graded_rule"]["paired_t"]["n"] == 3
    bs = sig["full_89_graded_rule"]["bootstrap"]
    assert bs["ci_low"] <= bs["mean"] <= bs["ci_high"]
    assert sig["wave9_reported"]["p"] == 0.05
    # 全正差异 → CI 不含 0
    pos = ea.e2.bootstrap_ci([0.2, 0.3, 0.25, 0.35, 0.1])
    assert pos["significant"] is True and pos["ci_low"] > 0
