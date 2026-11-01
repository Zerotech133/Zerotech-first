# -*- coding: utf-8 -*-
"""guard 拦截样本人工抽查清单（wave20 CC）回归测试。

覆盖：
  1) 清单 schema：12 条、review_id 唯一、逐条含问题/guard_action/issues/原答案摘要/guard后答案/引用/指标/勾选项/备注；
  2) 分组统计：6 pass + 6 fallback、拦截原因分组求和=6、issue_counts 与源文件一致；
  3) 优先抽查项：priority_items 排序 + focus 三个方向（信息量/引用真实性/推断过多）与指标一致；
  4) 与源数据一致且**不修改源数据**（source sha256 对齐、条目 action/id 对齐）；
  5) Markdown 审核稿含分组与 ☐通过/☐需修改/☐驳回。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
R = REPO / "research" / "reports"
SRC = R / "kb_qa_guard_20261101.json"
REVIEW_JSON = R / "kb_qa_guard_review_20261101.json"
REVIEW_MD = R / "kb_qa_guard_review_20261101.md"


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8-sig"))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def review() -> dict:
    return load(REVIEW_JSON)


def source() -> dict:
    return load(SRC)


# ---------------------------------------------------------------------------
# 1) 清单 schema
# ---------------------------------------------------------------------------
def test_review_schema_and_items():
    rev = review()
    assert rev["date"] == "20261101"
    assert rev["policy"]["sample_total"] == 12
    assert rev["policy"]["fallback_count"] == 6
    assert rev["policy"]["pass_count"] == 6
    assert rev["policy"]["modifies_source_data"] is False
    items = rev["items"]
    assert len(items) == 12
    ids = [it["review_id"] for it in items]
    assert len(set(ids)) == 12 and ids[0].startswith("GR-20261101-")
    for it in items:
        for key in ("question", "guard_action", "guard_issues", "before", "after",
                    "citations", "raw_answer_summary", "guarded_answer", "review"):
            assert key in it, f"{it.get('review_id')} 缺少 {key}"
        assert it["guard_action"] in ("pass", "fallback")
        assert it["citations"]["after_total"] == it["after"]["citation_total"]
        assert it["citations"]["after_valid"] == (source()["comparison"][it["source_index"]]["after"].get("citation_valid") or [])
        for m in ("citation_resolvable_rate", "support_rate", "inference_ratio", "hallucination_rate"):
            assert it["after"][m] is not None
        rv = it["review"]
        assert rv["status"] == "pending_review"
        assert rv["checkboxes"] == {"pass": False, "modify": False, "reject": False}
        assert isinstance(rv["notes"], str)
        assert it["raw_answer_summary"] and it["guarded_answer"]
    # 清单文件不得就地修改源文件：source sha 与当前源文件一致
    assert rev["source"]["sha256"] == sha(SRC)


# ---------------------------------------------------------------------------
# 2) 分组统计
# ---------------------------------------------------------------------------
def test_groups_and_counts():
    rev = review()
    src = source()
    items = rev["items"]
    fallback = [it for it in items if it["guard_action"] == "fallback"]
    assert len(fallback) == 6
    groups = rev["groups"]
    assert len(groups) == 2
    grouped_ids = [rid for g in groups for rid in g["item_ids"]]
    assert len(grouped_ids) == 6 and len(set(grouped_ids)) == 6
    by_id = {it["review_id"]: it for it in items}
    for g in groups:
        assert g["count"] == len(g["item_ids"])
        assert g["reason_label"]
        for rid in g["item_ids"]:
            assert by_id[rid]["guard_action"] == "fallback"
            assert tuple(by_id[rid]["guard_issues"]) == tuple(g["issue_combo"])
    # issue 级计数与源文件 issues 一致
    from collections import Counter
    expected = Counter(k for it in fallback for k in it["guard_issues"])
    assert rev["summary"]["issue_counts"] == dict(expected)
    assert rev["summary"]["issue_counts"] == src["issues"]
    assert rev["summary"]["fallback"] == 6 and rev["summary"]["pass"] == 6


# ---------------------------------------------------------------------------
# 3) 优先抽查项与三个 focus
# ---------------------------------------------------------------------------
def test_priority_and_focus():
    rev = review()
    items = rev["items"]
    fallback = [it for it in items if it["guard_action"] == "fallback"]
    prio = rev["summary"]["priority_items"]
    assert len(prio) == 6
    scores = [p["score"] for p in prio]
    assert scores == sorted(scores, reverse=True)
    assert all(p["reasons"] for p in prio)
    # focus 与指标口径一致
    focus = rev["summary"]["focus"]
    inf_ids = [it["review_id"] for it in fallback if (it["after"]["inference_ratio"] or 0) >= 0.6]
    assert focus["inference_excess"]["item_ids"] == inf_ids
    assert 1 <= len(inf_ids) <= 6
    info_ids = [it["review_id"] for it in fallback if (it["info_loss_ratio"] or 0) > 0]
    assert focus["info_loss"]["item_ids"] == info_ids
    for it in fallback:
        assert it["guarded_answer_len"] == len(it["guarded_answer"])
        assert it["raw_answer_len"] == len(it["raw_answer"])
        assert 0 <= (it["priority_score"] or 0) <= 100
    # pass 项不进入优先列表
    prio_ids = {p["review_id"] for p in prio}
    assert prio_ids <= {it["review_id"] for it in fallback}


# ---------------------------------------------------------------------------
# 4) 与源数据一致、不改源数据
# ---------------------------------------------------------------------------
def test_consistency_with_source_and_no_modification():
    rev = review()
    src = source()
    assert rev["source"]["sha256"] == sha(SRC), "源文件 sha256 变化（应只读）"
    assert rev["policy"]["modifies_source_data"] is False
    rows = src["comparison"]
    assert len(rows) == 12
    for it in rev["items"]:
        row = rows[it["source_index"]]
        assert it["id"] == row["id"]
        assert it["question"] == row["question"]
        assert it["guard_action"] == row["after"]["guard_action"]
        assert it["guard_issues"] == (row["after"].get("guard_issues") or [])
        # guard 后答案取自源数据（未被改写）
        assert it["guarded_answer"] == (row.get("guarded_answer") or "")
        assert it["raw_answer"] == (row.get("raw_answer") or "")


# ---------------------------------------------------------------------------
# 5) Markdown 审核稿
# ---------------------------------------------------------------------------
def test_markdown_contains_review_columns():
    md = REVIEW_MD.read_text(encoding="utf-8")
    for marker in ("guard 拦截样本人工抽查清单", "建议优先抽查项", "6 条拦截样本按原因分组",
                   "全量抽查清单", "☐通过", "☐需修改", "☐驳回", "不构成投资建议"):
        assert marker in md, marker
    # 12 条 review_id 均出现在 md
    for it in review()["items"]:
        assert it["review_id"] in md
