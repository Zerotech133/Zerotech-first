# -*- coding: utf-8 -*-
"""tools/tests/test_eval_v3_merge.py — 评测集 v3 + 合并复评（wave19 支线 BG）。

覆盖（任务验收 ≥3）：
  1. v3 资产校验：eval_set_20261101_v3 存在、version=3、n=102（89+13）、hash/规范校验通过、
     manifest 含 v3 条目且 hash 一致、v2 资产未被改动；
  2. 新查询覆盖：13 条 V3 查询均含 graded-2 且 expected_units 非空；两组 merged-concept
     盲区（CG-003/CG-004）均被 v3 查询覆盖（merge 复评 blind_group_count=0）；
  3. 合并复评 schema：merge_sim_v3 JSON 含 baseline/merged/comparison/new_queries_subset/
     affected_queries/coverage/conclusion；新增子集 n=13 且 before/after 指标齐全；
     结论 stance 合法、受影响查询有逐条 delta；
  4. 真实数据未改：units 总数仍 498、v2 资产 hash 仍为冻结值。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_eval_v3_merge.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))

import eval_registry as er  # noqa: E402
import eval_set_v3 as v3  # noqa: E402

V2_HASH = "1fa417f9e720cb6d4142432cbd1073ffc66d93905c542eadeed8fc1ea8519e0f"


def _require_outputs():
    if not v3.V3_PATH.exists():
        pytest.fail(f"v3 资产缺失: {v3.V3_PATH}（先跑 eval_set_v3.py freeze）")
    if not v3.MERGE_V3_JSON.exists():
        pytest.fail(f"合并复评缺失: {v3.MERGE_V3_JSON}（先跑 eval_set_v3.py merge-eval）")


# ------------------------------------------------------------------ 1. v3 资产
def test_v3_asset_and_manifest():
    _require_outputs()
    asset = json.loads(v3.V3_PATH.read_text(encoding="utf-8"))
    assert asset["set_id"] == "eval_set_20261101_v3"
    assert asset["version"] == 3
    assert asset["n_queries"] == 102 == len(asset["queries"])
    assert len(asset["content_hash"]) == 64
    # hash/结构/规范校验
    check = er.validate_asset(asset, verify_hash=True)
    assert check["ok"] is True, check["errors"]
    assert er.verify(v3.V3_PATH)["ok"] is True
    # manifest：v2 + v3 均在，v3 hash 与资产一致
    manifest = json.loads((v3.EVAL_DIR / "manifest.json").read_text(encoding="utf-8"))
    versions = {int(s["version"]): s for s in manifest["sets"]}
    assert 2 in versions and 3 in versions
    assert versions[3]["content_hash"] == asset["content_hash"]
    assert versions[3]["n_queries"] == 102
    # v2 资产未改动
    v2 = json.loads(v3.V2_PATH.read_text(encoding="utf-8"))
    assert v2["n_queries"] == 89 and v2["content_hash"] == V2_HASH
    assert er.verify(v3.V2_PATH)["ok"] is True


# ------------------------------------------------------------------ 2. 新查询覆盖
def test_new_queries_cover_blind_groups():
    _require_outputs()
    asset = json.loads(v3.V3_PATH.read_text(encoding="utf-8"))
    new = [q for q in asset["queries"] if str(q["id"]).startswith("V3-")]
    assert len(new) == 13, f"新增查询应为 13 条，实际 {len(new)}"
    for q in new:
        assert q["graded"]["2"], f"{q['id']} 缺 grade-2"
        assert q["expected_units"], f"{q['id']} expected_units 为空"
        assert all(isinstance(e["unit"], int) and e["grade"] in (1, 2)
                   for e in q["expected_units"])
    # 两组 merged-concept 概念形式均被 v3 查询提及（q 或 graded 文本）
    for form in v3.MERGED_CONCEPT_FORMS:
        hit = [q["id"] for q in asset["queries"]
               if form in str(q.get("q") or "")
               or form in " ".join(str(k) for gd in (q.get("graded") or {}).values()
                                   for k in (gd or []))]
        assert hit, f"merged-concept 未被任何查询覆盖: {form}"
    # 合并复评侧的覆盖结论：盲区归零
    merge = json.loads(v3.MERGE_V3_JSON.read_text(encoding="utf-8"))
    assert merge["coverage"]["blind_groups_v3"] == 0
    assert merge["coverage"]["blind_groups_v2"] == 2


# ------------------------------------------------------------------ 3. 合并复评 schema
def test_merge_v3_schema_and_affected():
    _require_outputs()
    rep = json.loads(v3.MERGE_V3_JSON.read_text(encoding="utf-8"))
    for key in ("eval_set", "merge_plan", "baseline_summary", "merged_summary",
                "comparison_stats", "new_queries_subset", "affected_queries",
                "coverage", "conclusion", "real_data_unchanged"):
        assert key in rep, f"merge_sim_v3 缺少字段 {key}"
    assert rep["eval_set"]["set_id"] == "eval_set_20261101_v3"
    assert rep["eval_set"]["n_queries"] == 102
    assert rep["merge_plan"]["merge_groups"] == 2
    bw, aw = rep["baseline_summary"], rep["merged_summary"]
    for k in ("precision_at_k", "coverage", "hit_rate", "ndcg_at_k"):
        assert k in bw and k in aw
    nw = rep["new_queries_subset"]
    assert nw["before"]["n"] == 13 and nw["after"]["n"] == 13
    assert rep["conclusion"]["stance"] in {"merge_worth", "merge_mixed", "merge_neutral"}
    # 受影响查询逐条含 delta
    for a in rep["affected_queries"]:
        assert "precision_at_k" in a["delta"] and "ndcg_at_k" in a["delta"]
        assert str(a["query_id"]).startswith("V3-") if a["query_id"] in v3.NEW_IDS else True
    # v2 旧复评对照存在
    assert rep["v2_merge_sim"]["verdict"] in {"neutral", None}


# ------------------------------------------------------------------ 4. 真实数据未改
def test_real_data_unchanged():
    _require_outputs()
    units = json.loads(v3.UNITS_PATH.read_text(encoding="utf-8"))
    assert units.get("total") == 498
    merge = json.loads(v3.MERGE_V3_JSON.read_text(encoding="utf-8"))
    assert merge["real_data_unchanged"] is True
    # v2 资产 hash 仍是冻结值（v3 不动 v2）
    v2 = json.loads(v3.V2_PATH.read_text(encoding="utf-8"))
    assert v2["content_hash"] == V2_HASH
