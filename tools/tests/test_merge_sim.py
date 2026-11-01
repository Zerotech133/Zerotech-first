# -*- coding: utf-8 -*-
"""concept 合并影响模拟（merge_sim）回归测试。

覆盖：
  1) 合并计划纯函数（按 alias_map 只并 action=merge 组：保留主单元/剔除被合并单元/alias 重映射）；
  2) 内存索引重建（剔除单元不进入索引、保持顺序）+ 问题 source_unit_id 重映射；
  3) 对比逻辑 & 报告 schema（@1/@5/平均排名 delta、受影响问题、结论/建议）；
  4) Markdown 渲染；
  5) 集成 dry-run（注入假检索，快）：merge 2 组、真实数据未写；
  6) --write 产物 + 真实数据 SHA1 不变（real_data_unchanged=true）。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "kb_corpus" / "merge_sim.py"

_spec = importlib.util.spec_from_file_location("merge_sim_tool", SCRIPT)
ms = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ms)  # type: ignore[union-attr]


def fake_eval(hit1: bool, hit5: bool, rank, qid: str = "q1", concept: str = "X") -> dict:
    item = {"id": qid, "question": f"{qid} 问题", "concept": concept, "source_unit_id": "s1",
            "top1_is_source": hit1, "hit_source": hit5, "source_rank": rank}
    return {"summary": {"total": 1, "hit_at_1": int(hit1), "hit_at_5": int(hit5),
                        "hit_rate_at_1": float(hit1), "hit_rate_at_5": float(hit5),
                        "avg_source_rank": rank},
            "items": [item], "elapsed_seconds": 0.01}


# ---------------------------------------------------------------------------
# 1) plan_merge 纯函数
# ---------------------------------------------------------------------------
def test_plan_merge_pure():
    units_all = [
        {"book": "rp_A", "chapter": "5.", "p1": 5, "concept": "东盟MSME", "claim": "同一句结论。"},
        {"book": "rp_B", "chapter": "5.", "p1": 5, "concept": "东盟MSME", "claim": "同一句结论。"},
        {"book": "rp_C", "chapter": "1.", "p1": 1, "concept": "其他", "claim": "另一条。"},
    ]
    u0 = ms.retrieval_unit_id(units_all[0])
    u1 = ms.retrieval_unit_id(units_all[1])
    dedup = {"groups": [
        {"group_id": "CG-003", "concept_forms": ["东盟MSME"], "action": "merge",
         "primary_unit_id": "AL-X", "units": [
             {"unit_id": "al-1", "is_primary": False, "book": "rp_A", "chapter": "5.", "page_start": 5, "claim": "同一句结论。"},
             {"unit_id": "al-2", "is_primary": True, "book": "rp_B", "chapter": "5.", "page_start": 5, "claim": "同一句结论。"},
         ]},
        {"group_id": "CG-001", "concept_forms": ["量学理论"], "action": "annotate",
         "primary_unit_id": "AL-Y", "units": [{"unit_id": "al-3", "is_primary": True, "book": "书", "page_start": 1, "claim": "x"},
                                              {"unit_id": "al-4", "is_primary": False, "book": "书", "page_start": 2, "claim": "y"}]},
    ]}
    plan = ms.plan_merge(dedup, units_all)
    assert plan["merge_groups"] == 1, "只处理 action=merge 组（annotate 组不合并）"
    assert plan["units_before"] == 3 and plan["units_after"] == 2
    assert plan["alias_remap"] == {u0: u1}
    assert plan["dropped_ids"] == [u0]
    assert plan["groups"][0]["primary_unit_id"] == u1
    assert plan["groups"][0]["dropped_unit_ids"] == [u0]


# ---------------------------------------------------------------------------
# 2) 索引重建 + 问题重映射
# ---------------------------------------------------------------------------
def test_index_rebuild_and_remap():
    units = [{"id": "a", "claim": "x"}, {"id": "b", "claim": "y"}, {"id": "c", "claim": "z"}]
    plan = {"dropped_ids": ["b"], "alias_remap": {"b": "a"}}
    merged = ms.apply_merge_to_index(units, plan)
    assert [u["id"] for u in merged] == ["a", "c"], "剔除被合并单元且保持原顺序"
    idx = ms.build_index(merged)
    assert "b" not in idx and idx["a"]["claim"] == "x"

    questions = [{"id": "q1", "question": "Q1", "source_unit_id": "b"},
                 {"id": "q2", "question": "Q2", "source_unit_id": "c"}]
    remapped, records = ms.remap_questions(questions, plan["alias_remap"])
    assert remapped[0]["source_unit_id"] == "a"
    assert remapped[0]["_original_source_unit_id"] == "b"
    assert remapped[1]["source_unit_id"] == "c"
    assert records == [{"question_id": "q1", "from": "b", "to": "a"}]


# ---------------------------------------------------------------------------
# 3) 对比逻辑 + 报告 schema
# ---------------------------------------------------------------------------
def test_compare_and_report_schema():
    baseline = fake_eval(False, True, 3)
    merged_alias = fake_eval(True, True, 1)
    merged_raw = fake_eval(False, False, None)
    plan = {"merge_groups": 1, "units_before": 10, "units_after": 9, "dropped_ids": ["s0"],
            "alias_remap": {"s0": "s1"},
            "groups": [{"group_id": "CG-004", "concept_forms": ["X"], "primary_unit_id": "s1",
                        "dropped_unit_ids": ["s0"]}]}
    cmp = ms.compare_runs(baseline, merged_alias, merged_raw, plan)
    assert cmp["deltas"]["hit_at_1"]["baseline"] == 0
    assert cmp["deltas"]["hit_at_1"]["merged_alias"] == 1
    assert cmp["deltas"]["hit_at_1"]["delta_alias"] == 1
    assert cmp["deltas"]["hit_at_5"]["delta_raw"] == -1
    assert len(cmp["affected_questions"]) == 1
    assert cmp["affected_questions"][0]["delta"] == "improved"

    report = ms.build_report(units_all_meta={"path": "x", "sha1_before": "abc"},
                             questions_path="q.json", plan=plan, baseline=baseline,
                             merged_alias=merged_alias, merged_raw=merged_raw,
                             aliases=[{"question_id": "q1", "from": "s0", "to": "s1"}],
                             generated_at="2026-11-01T10:00:00+08:00")
    for key in ("schema_version", "date", "generated_at", "mode", "source", "policy",
                "merge_plan", "alias_remap_records", "results", "comparison", "conclusion",
                "verdict", "recommendation", "disclaimer"):
        assert key in report, key
    assert report["mode"] == "in_memory_simulation"
    assert report["policy"]["modifies_real_data"] is False
    assert report["verdict"] == "worth_merging"
    assert report["recommendation"]["preconditions"]
    assert "不构成投资建议" in report["disclaimer"]
    assert report["results"]["baseline"]["summary"]["hit_at_1"] == 0


# ---------------------------------------------------------------------------
# 4) Markdown 渲染
# ---------------------------------------------------------------------------
def test_markdown_render():
    plan = {"merge_groups": 1, "units_before": 10, "units_after": 9, "dropped_ids": ["s0"],
            "alias_remap": {"s0": "s1"},
            "groups": [{"group_id": "CG-004", "concept_forms": ["跨境电子商务面临的四大挑战"],
                        "primary_unit_id": "s1", "dropped_unit_ids": ["s0"]}]}
    report = ms.build_report(units_all_meta={"path": "x"}, questions_path="q.json", plan=plan,
                             baseline=fake_eval(False, True, 3), merged_alias=fake_eval(True, True, 1),
                             merged_raw=fake_eval(False, False, None), aliases=[])
    md = ms.render_markdown(report)
    assert "# concept 合并影响模拟" in md
    assert "@1 / @5 / 平均来源排名" in md
    assert "受影响问题" in md
    assert "跨境电子商务面临的四大挑战" in md
    assert "前置条件" in md and "风险" in md
    assert "不改真实数据" in md or "内存模拟" in md


# ---------------------------------------------------------------------------
# 5) 集成 dry-run（注入假检索，快）：真实 merge 组、数据不写
# ---------------------------------------------------------------------------
def test_run_dry_run_with_injected_search():
    sha_before = hashlib.sha1(ms.UNITS_ALL.read_bytes()).hexdigest()
    report = ms.run(limit=1, write=False, search_fn=lambda q, k: [])
    assert report["mode"] == "in_memory_simulation"
    assert report["merge_plan"]["merge_groups"] == 2, "AL 审计可合并 2 组"
    assert report["merge_plan"]["units_before"] == report["merge_plan"]["units_after"] + 2
    assert report["index_rebuild"]["ok"] is True
    assert report["index_rebuild"]["after"] == report["index_rebuild"]["before"] - 2
    assert report["real_data_unchanged"] is True, "dry-run 也应自证真实数据未修改"
    assert hashlib.sha1(ms.UNITS_ALL.read_bytes()).hexdigest() == sha_before


# ---------------------------------------------------------------------------
# 6) --write 产物 + 真实数据 SHA1 不变
# ---------------------------------------------------------------------------
def test_run_write_outputs_and_data_unchanged(tmp_path):
    sha_before = hashlib.sha1(ms.UNITS_ALL.read_bytes()).hexdigest()
    report = ms.run(limit=1, write=True, search_fn=lambda q, k: [], out_dir=tmp_path)
    assert report["real_data_unchanged"] is True
    assert hashlib.sha1(ms.UNITS_ALL.read_bytes()).hexdigest() == sha_before, "绝不写 kb_units_all.json"
    jp = tmp_path / "merge_sim_20261101.json"
    mp = tmp_path / "merge_sim_20261101.md"
    assert jp.is_file() and mp.is_file()
    saved = json.loads(jp.read_text(encoding="utf-8"))
    assert saved["merge_plan"]["merge_groups"] == 2
    assert "concept 合并影响模拟" in mp.read_text(encoding="utf-8")
