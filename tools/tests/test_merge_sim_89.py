# -*- coding: utf-8 -*-
"""89 查询合并影响模拟（merge_sim_89）回归测试。

覆盖：
  1) 冻结评测集资产加载（set_id/content_hash/89 查询/graded）；
  2) 内存合并视图 + expected_units 索引 alias 重映射；
  3) 合并前后指标对比 schema（配对 t / bootstrap CI / McNemar / 受影响查询）与判定逻辑；
  4) 限量子集真跑（limit=3）+ 报告 schema（head 带 set_id+content_hash）+ 真实数据不变 + 落盘。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "kb_corpus" / "merge_sim_89.py"

_spec = importlib.util.spec_from_file_location("merge_sim_89_tool", SCRIPT)
m89 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m89)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 1) 冻结评测集资产
# ---------------------------------------------------------------------------
def test_load_frozen_eval_set():
    es, errors = m89.load_eval_set()
    assert errors == [], errors
    assert es["set_id"] == "eval_set_20261101_v2"
    assert es["content_hash"] == "1fa417f9e720cb6d4142432cbd1073ffc66d93905c542eadeed8fc1ea8519e0f"
    assert es["n_queries"] == 89 and len(es["queries"]) == 89
    assert es["file_sha256"] and len(es["file_sha256"]) == 64
    q = es["queries"][0]
    assert q["id"] and q["q"] and isinstance(q.get("graded"), dict)
    assert "2" in q["graded"]
    # 缺失文件容错
    es2, err2 = m89.load_eval_set(REPO / "no_such_eval_set.json")
    assert err2 and es2["queries"] == []


# ---------------------------------------------------------------------------
# 2) 合并视图 + expected_units 重映射
# ---------------------------------------------------------------------------
def test_build_views_and_expected_remap():
    ev, e2, sv, merge_mod = m89.load_stack()
    units = [
        {"book": "rp_A", "chapter": "5.", "p1": 5, "concept": "东盟MSME", "claim": "同一句结论。", "evidence": "e1"},
        {"book": "rp_B", "chapter": "5.", "p1": 5, "concept": "东盟MSME", "claim": "同一句结论。", "evidence": "e2"},
        {"book": "rp_C", "chapter": "1.", "p1": 1, "concept": "其他", "claim": "另一条。", "evidence": "e3"},
    ]
    u0 = merge_mod.retrieval_unit_id(units[0])
    merge_mod.retrieval_unit_id(units[1])
    dedup = {"groups": [{"group_id": "CG-003", "concept_forms": ["东盟MSME"], "action": "merge",
                         "primary_unit_id": "AL", "units": [
                             {"unit_id": "al-1", "is_primary": False, "book": "rp_A", "chapter": "5.", "page_start": 5, "claim": "同一句结论。"},
                             {"unit_id": "al-2", "is_primary": True, "book": "rp_B", "chapter": "5.", "page_start": 5, "claim": "同一句结论。"},
                         ]}]}
    views = m89.build_views(units, dedup, merge_mod)
    assert views["plan"]["merge_groups"] == 1
    assert views["dropped_ids"] == {u0}
    assert len(views["merged_units"]) == 2 and len(views["baseline_units"]) == 3
    # expected_units 中 unit=0（被剔除）→ 重映射到主单元索引 1
    queries = [{"id": "Q01", "q": "东盟", "expected_units": [{"unit": 0, "grade": 2}, {"unit": 2, "grade": 1}]}]
    remapped, records = m89.remap_expected_units(queries, units, views, merge_mod)
    ids = [item["unit"] for item in remapped[0]["expected_units"]]
    assert 1 in ids and 0 not in ids and 2 in ids
    assert records and records[0]["from"] == 0 and records[0]["to"] == 1


# ---------------------------------------------------------------------------
# 3) 对比 schema / 显著性 / 判定
# ---------------------------------------------------------------------------
def _view(rows: list[dict], summary: dict | None = None) -> dict:
    return {"rows": rows, "summary": summary or {}, "by_type": {}, "eval_meta": {"topk": 5, "n_units": 10}}


def test_compare_schema_and_decide():
    ev, e2, sv, merge_mod = m89.load_stack()
    base_rows = [{"id": "Q01", "query": "a", "type": "主题", "precision_at_k": 0.4,
                  "coverage": 0.5, "hit": 1, "ndcg_at_k": 0.5, "grades": [2, 0]},
                 {"id": "Q02", "query": "b", "type": "主题", "precision_at_k": 0.6,
                  "coverage": 0.7, "hit": 1, "ndcg_at_k": 0.6, "grades": [2, 2]}]
    merged_rows = [dict(base_rows[0]), dict(base_rows[1], precision_at_k=0.8, ndcg_at_k=0.9)]
    cmp = m89.compare_views(_view(base_rows), _view(merged_rows), ev, e2)
    assert cmp["stats"]["n_pairs"] == 2
    st = cmp["stats"]
    for key in ("precision_at_k", "ndcg_at_k"):
        assert "mean_delta" in st[key] and "paired_t" in st[key] and "bootstrap" in st[key]
        assert "ci_low" in st[key]["bootstrap"] and "p_two_sided" in st[key]["paired_t"]
    assert st["precision_at_k"]["mean_delta"] == 0.1
    assert "mcnemar" in st["hit_rate"]
    assert len(cmp["affected_queries"]) == 1
    assert cmp["affected_queries"][0]["query_id"] == "Q02"

    # 判定分支
    verdict, text = m89.decide(cmp)
    assert verdict in ("worth_merging_robustly", "worth_merging_directionally")
    neutral = m89.compare_views(_view(base_rows), _view(base_rows), ev, e2)
    assert m89.decide(neutral)[0] == "neutral"
    worse = m89.compare_views(_view(base_rows), _view([dict(r, precision_at_k=0.0, ndcg_at_k=0.0)
                                                      for r in base_rows]), ev, e2)
    assert m89.decide(worse)[0] == "not_worth_merging_now"


# ---------------------------------------------------------------------------
# 4) 限量子集真跑 + 报告 head + 真实数据不变 + 落盘
# ---------------------------------------------------------------------------
def test_run_limited_schema_and_outputs(tmp_path):
    sha_units = m89.sha256_file(m89.UNITS_ALL)
    sha_eval = m89.sha256_file(m89.EVAL_SET)
    report = m89.run(limit=3, write=True, out_dir=tmp_path)
    head = report["head"]
    assert head["eval_set_id"] == "eval_set_20261101_v2"
    assert head["eval_set_content_hash"].startswith("1fa417f9")
    assert head["n_queries"] == 89  # 资产声明 89（limit 只影响评测条数）
    assert report["mode"] == "in_memory_merge_simulation"
    assert report["policy"]["modifies_real_data"] is False
    assert report["merge_plan"]["merge_groups"] == 2
    # 2026-11-01 15:14 知识库正式入库后 units=516，合并 2 组后 514。
    assert report["merge_plan"]["units_before"] == 516
    assert report["merge_plan"]["units_after"] == 514
    assert report["real_data_unchanged"] is True
    assert report["results"]["baseline"]["summary"]["n"] == 3
    for key in ("precision_at_k", "coverage", "hit_rate", "ndcg_at_k"):
        assert isinstance(report["results"]["baseline"]["summary"][key], (int, float))
        assert isinstance(report["results"]["merged"]["summary"][key], (int, float))
    assert "stats" in report["comparison"] and "affected_queries" in report["comparison"]
    assert report["verdict"] in ("worth_merging_robustly", "worth_merging_directionally", "neutral",
                                 "not_worth_merging_now")
    # 真实数据未被修改
    assert m89.sha256_file(m89.UNITS_ALL) == sha_units
    assert m89.sha256_file(m89.EVAL_SET) == sha_eval
    # 产物
    jp = tmp_path / "merge_sim_89_20261101.json"
    mp = tmp_path / "merge_sim_89_20261101.md"
    assert jp.is_file() and mp.is_file()
    saved = json.loads(jp.read_text(encoding="utf-8"))
    assert saved["head"]["eval_set_content_hash"].startswith("1fa417f9")
    md = mp.read_text(encoding="utf-8")
    assert "89 查询冻结评测" in md and "content_hash" in md and "配对 t" in md
