# -*- coding: utf-8 -*-
"""v4 fallback 3 条人工可审清单：产物契约测试（只读，不调 LLM）。

覆盖：
  1. 加载：JSON/MD 存在且可解析；
  2. schema：3 条样本、必需字段（问题/v4 指标/历史对照/审读清单/判定三选项）；
  3. 汇总一致性：条数、guard_issues 分布、判定计数、与源复评 JSON 的 fallback 行一致；
  4. 数据诚实性：原文未持久化如实标注、清单 5 项齐全、MD 关键段落齐全。
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REVIEW_JSON = REPO / "research" / "reports" / "v4_fallback_review_20261101.json"
REVIEW_MD = REPO / "research" / "reports" / "v4_fallback_review_20261101.md"
RECHECK_JSON = REPO / "research" / "reports" / "kb_qa_v4_default_recheck_20261101.json"


def load_review() -> dict:
    assert REVIEW_JSON.exists(), f"缺少审读 JSON：{REVIEW_JSON}"
    return json.loads(REVIEW_JSON.read_text(encoding="utf-8"))


def test_artifacts_load_and_schema():
    data = load_review()
    assert data["kind"] == "v4_fallback_review"
    assert data["schema_version"] == 1
    for key in ("source", "summary", "samples", "default_impact", "note"):
        assert key in data, key
    samples = data["samples"]
    assert len(samples) == 3
    for s in samples:
        for key in ("id", "question", "v4_raw", "history_v1_multi", "review_checklist", "verdict"):
            assert key in s, key
        raw = s["v4_raw"]
        for key in ("status", "answer_length", "citations", "distinct_cited_units",
                    "citation_resolvable_rate", "support_rate", "inference_ratio", "guard_issues"):
            assert key in raw, key
        v = s["verdict"]
        assert set(v) >= {"acceptable", "fix_prompt", "fix_guard", "primary", "checkbox"}
        assert "☐" in v["checkbox"] and "☑" in v["checkbox"]
        assert v["primary"] in ("acceptable", "fix_prompt", "fix_guard")
    assert REVIEW_MD.exists() and REVIEW_MD.stat().st_size > 1000


def test_summary_consistency():
    data = load_review()
    samples, summary = data["samples"], data["summary"]
    assert summary["total_fallback"] == len(samples) == 3
    assert summary["fallback_ratio_of_recheck"] == 0.15
    assert summary["guard_issues_breakdown"] == {"insufficient_cited_units": 3}
    counts = {"acceptable": 0, "fix_prompt": 0, "fix_guard": 0}
    for s in samples:
        counts[s["verdict"]["primary"]] += 1
    assert summary["verdict_counts"] == counts
    assert counts["acceptable"] + counts["fix_prompt"] + counts["fix_guard"] == 3
    assert summary["all_single_cited_unit"] is True
    assert all((s["v4_raw"]["distinct_cited_units"] or 0) < 2 for s in samples)
    assert summary["all_resolvable"] is True


def test_matches_source_recheck_fallbacks():
    review = load_review()
    review_ids = {s["id"] for s in review["samples"]}
    src = json.loads(RECHECK_JSON.read_text(encoding="utf-8"))
    fallback_rows = [r for r in src["rows"] if r["status"] == "fallback"]
    src_ids = {r["id"] for r in fallback_rows}
    assert review_ids == src_ids, "review 样本应与复评 fallback 行完全一致"
    for s in review["samples"]:
        raw = s["v4_raw"]
        assert raw["guard_issues"] == ["insufficient_cited_units"]
        assert raw["citation_resolvable_rate"] == 1.0
        assert raw["support_rate"] == 1.0
        assert raw["guard_action"] == "fallback"
        assert 0.0 <= raw["inference_ratio"] <= 0.7  # 未触发 inference_heavy


def test_checklist_items_and_honesty():
    data = load_review()
    required_items = ("original_multi_answer", "guard_fallback_excerpt", "citation_support",
                      "inference_ratio_assessment", "info_loss")
    for s in data["samples"]:
        checklist = s["review_checklist"]
        for item in required_items:
            assert item in checklist, item
            assert checklist[item]
        # 数据诚实性：原文未持久化必须如实标注
        assert "未持久化" in checklist["original_multi_answer"]
        assert "未持久化" in checklist["guard_fallback_excerpt"]
    # 默认影响判断：3 条 fallback 不构成回退
    impact = data["default_impact"]
    assert "维持 v4" in impact["verdict"]
    assert impact["action_items"]
    md = REVIEW_MD.read_text(encoding="utf-8")
    for anchor in ("汇总判定", "逐条明细", "汇总建议与对 v4 默认的影响判断", "数据局限"):
        assert anchor in md, anchor
    assert "☑" in md and "☐" in md
