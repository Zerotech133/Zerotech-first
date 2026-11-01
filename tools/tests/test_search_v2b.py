# -*- coding: utf-8 -*-
"""tools/tests/test_search_v2b.py — 检索 v2b（软 AND + 同义词低权重，wave12 支线 AH）。

覆盖（任务验收 ≥4）：
  1. 软 AND 语义：strict 返回全词命中、soft（0.5）保留部分命中且全词命中排前、any 最宽；
  2. 阈值语义：soft_cov=0.7 对 2 词查询等价 strict（ceil(0.7×2)=2）；
  3. 同义词低权重：原词命中得分 > 同义词（别名）命中得分（×0.6）；
  4. 与 v2 一致性：v2b strict 的单元打分/排序与 search_v2.SearchV2 完全一致；
  5. 冻结评测对比 schema：v1/v2/v2b 表、AND 子集、配对显著性、合入建议，JSON/MD 可序列化。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_search_v2b.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))

import search_v2 as sv  # noqa: E402
import search_v2b as v2b  # noqa: E402


def _units():
    return [
        {"book": "b", "chapter": "c", "concept": "快递 盈利 双主题",
         "claim": "快递企业盈利修复", "evidence": "盈利改善"},
        {"book": "b", "chapter": "c", "concept": "快递行业", "claim": "快递单量增长",
         "evidence": "快递"},
        {"book": "b", "chapter": "c", "concept": "企业盈利", "claim": "盈利水平提升",
         "evidence": "盈利"},
        {"book": "b", "chapter": "c", "concept": "无关内容", "claim": "其他", "evidence": "x"},
    ]


def _eng(mode="soft", soft_cov=v2b.DEFAULT_SOFT_COV, syn=None, weights=None):
    return v2b.SearchV2B(_units(), synonyms=syn, weights=weights,
                         and_mode=mode, soft_cov=soft_cov)


# ------------------------------------------------------------------ 1. 软 AND 语义
def test_soft_and_semantics():
    q = "快递 盈利"
    strict = _eng("strict")
    soft = _eng("soft")
    any_ = _eng("any")
    strict_ids = [r["concept"] for r in strict.search_units(q)]
    soft_ids = [r["concept"] for r in soft.search_units(q)]
    any_ids = [r["concept"] for r in any_.search_units(q)]
    # strict 只返回两词都命中的单元
    assert "无关内容" not in strict_ids
    assert all(("快递" in c and "盈利" in c) for c in strict_ids)
    # soft 保留部分命中（只有快递 / 只有盈利），且全词命中排最前
    assert "快递行业" in soft_ids and "企业盈利" in soft_ids
    assert soft_ids[0] == "快递 盈利 双主题"
    assert len(soft_ids) >= len(strict_ids)
    # any 最宽（≥1 词命中），包含 soft 的结果
    assert set(soft_ids) <= set(any_ids)


# ------------------------------------------------------------------ 2. soft_cov 语义
def test_soft_cov_07_equals_strict_for_two_terms():
    q = "快递 盈利"
    strict = _eng("strict").search_units(q)
    soft07 = _eng("soft", soft_cov=0.7).search_units(q)
    assert [r["concept"] for r in soft07] == [r["concept"] for r in strict]
    assert [r["score"] for r in soft07] == [r["score"] for r in strict]
    # 0.5 阈值则放松：2 词查询允许 1 词命中
    soft05 = _eng("soft", soft_cov=0.5).search_units(q)
    assert len(soft05) > len(strict)


# ------------------------------------------------------------------ 3. 同义词低权重
def test_synonym_weighting():
    units = [
        {"book": "b", "chapter": "c", "concept": "光伏行业", "claim": "光伏装机",
         "evidence": "x"},
        {"book": "b", "chapter": "c", "concept": "太阳能装机", "claim": "太阳能利用",
         "evidence": "x"},
    ]
    syn = sv.SynonymTable([["光伏", "太阳能"]])
    strict = v2b.SearchV2B(units, synonyms=syn, and_mode="strict")
    scores = {r["concept"]: r["score"] for r in strict.search_units("光伏")}
    # 原词命中权重 1.0、同义词命中 ×0.6 → 原词单元得分显著更高（非精确比例：
    # 原词命中有短语加分，同义词命中无）
    ratio = scores["光伏行业"] / scores["太阳能装机"]
    assert scores["光伏行业"] > scores["太阳能装机"]
    assert 1.05 < ratio < 2.0
    # 把同义词权重设为 1.0 后两者得分相等
    plain = v2b.SearchV2B(units, synonyms=syn, and_mode="strict", synonym_weight=1.0)
    plain_scores = {r["concept"]: r["score"] for r in plain.search_units("光伏")}
    assert plain_scores["太阳能装机"] == pytest.approx(plain_scores["光伏行业"], abs=1e-9)


# ------------------------------------------------------------------ 4. 与 v2 一致性
def test_strict_mode_matches_v2():
    units = _units()
    syn = sv.SynonymTable([["快递", "物流"]])
    # v2 默认已合入 soft+0.6；与 v2b strict 对照时需显式恢复旧严格基线
    base = sv.SearchV2(units, synonyms=syn, and_mode="strict", synonym_weight=1.0)
    mine = v2b.SearchV2B(units, synonyms=syn, and_mode="strict", synonym_weight=1.0)
    for q in ("快递 盈利", "快递|盈利", "无关"):
        a = [(r["concept"], r["score"]) for r in base.search_units(q)]
        b = [(r["concept"], r["score"]) for r in mine.search_units(q)]
        assert a == b, f"v2b strict 与 v2 不一致: {q}"


# ------------------------------------------------------------------ 5. 冻结评测对比 schema
def test_compare_engines_schema_and_md():
    units = _units()
    syn = sv.SynonymTable([["快递", "物流"]])
    queries = [
        {"id": "T1", "q": "快递 盈利", "type": "AND",
         "graded": {"2": ["快递"], "1": ["盈利"]}, "source": "wave10"},
        {"id": "T2", "q": "快递|企业盈利", "type": "OR",
         "graded": {"2": ["快递", "企业盈利"], "1": ["盈利"]}, "source": "wave10"},
        {"id": "T3", "q": "快递", "type": "主题",
         "graded": {"2": ["快递"], "1": ["物流"]}, "source": "wave10"},
    ]
    rep = v2b.compare_engines(units, syn, queries)
    rep["conclusion"] = v2b.build_conclusion(rep)
    assert rep["meta"]["schema_version"] == "search_v2b.v1"
    assert rep["meta"]["n_queries"] == 3
    for key in ("all", "AND"):
        assert key in rep["tables"]
        assert "v1" in rep["tables"][key]
        for eng in ("v2", "v2b_soft", "v2b_soft07", "v2b_any"):
            assert eng in rep["tables"][key]
    assert rep["parity_check"]["identical"] is True
    assert rep["parity_check"]["v2b_strict_parity_vs_v2_max_abs_diff"]["precision_at_k"] < 1e-9
    assert set(rep["significance"]["v2b_soft"]) == {"precision_at_k", "ndcg_at_k"}
    assert "merge_recommended" in rep["conclusion"]
    json.dumps(rep, ensure_ascii=False)   # 可序列化
    md = v2b.write_md(rep)
    for sec in ("整体", "AND 类", "显著性", "结论与合入建议"):
        assert sec in md
    assert "soft" in md and "同义词" in md
