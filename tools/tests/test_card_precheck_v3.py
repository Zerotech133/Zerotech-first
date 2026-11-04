# -*- coding: utf-8 -*-
"""第二轮 40 候选机器预审（card_precheck_v3）回归测试。

覆盖：
  1) candidate_to_entry：CP3 候选 → card_precheck 条目格式映射（review_id/q/a/出处/页码/unit）；
  2) 预审分布与规则：40 张全部有 pass/revise/reject 建议 + 理由 + 置信 + score，分布合计=40；
  3) 审核页数据回写：CP3 条目 precheck 与报告一致、40/40 非空；**第一轮 60 条不变**；stats.precheck 与条目统计一致；
  4) 不修改源数据（proposals/学习卡/清单 SHA256 前后一致）+ 幂等（重复构建分布一致）；
  5) 报告 schema/JSON/MD 输出可用。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "kb_corpus" / "card_precheck_v3.py"
REPORT_JSON = REPO / "research" / "reports" / "card_precheck_v3_20261101.json"
REPORT_MD = REPO / "research" / "reports" / "card_precheck_v3_20261101.md"
REVIEW_DATA = REPO / "portal" / "static" / "card_review_data.json"
PROPOSALS = REPO / "portal" / "static" / "card_proposals_v3.json"
CARDS = REPO / "portal" / "static" / "kb_learning_cards.json"
CHECKLIST = REPO / "research" / "reports" / "card_review_checklist_20261101.json"

_spec = importlib.util.spec_from_file_location("card_precheck_v3_tool", SCRIPT)
cp3 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cp3)  # type: ignore[union-attr]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8-sig"))


def report() -> dict:
    return load(REPORT_JSON)


def archived(d: dict) -> bool:
    """报告生成后 proposals/cards 已变化，按历史归档口径校验。"""
    return (d["source"]["proposals_sha256_before"] != sha(PROPOSALS)
            or d["source"]["learning_cards_sha256_before"] != sha(CARDS)
            or d["source"]["checklist_sha256_before"] != sha(CHECKLIST))


# ---------------------------------------------------------------------------
# 1) 候选 → 清单条目映射
# ---------------------------------------------------------------------------
def test_candidate_to_entry():
    c = load(PROPOSALS)["candidates"][0]
    e = cp3.candidate_to_entry(c)
    assert e["review_id"] == c["card_id"] and e["source"] == "CP3"
    for key in ("theme", "angle_cn", "q", "a", "concept", "evidence", "unit_id"):
        assert e[key] == c[key]
    assert e["page_start"] == c["page_start"] and e["page_end"] == c["page_end"]
    assert e["book"] == c["book"] and e["chapter"] == c["chapter"]
    assert "《" in e["source_ref"] and f"p{c['page_start']}-{c['page_end']}" in e["source_ref"]
    # 可被 card_precheck 规则直接消费
    cp = cp3.load_precheck_module()
    idx = cp.build_batch_index([e])
    r = cp.precheck_entry(e, [], idx)
    assert r["review_id"] == c["card_id"]
    assert r["suggested_verdict"] in ("pass", "revise", "reject")
    assert r["reasons"] and 0 <= r["score"] <= 100


# ---------------------------------------------------------------------------
# 2) 预审分布
# ---------------------------------------------------------------------------
def test_precheck_distribution():
    d = report()
    s = d["summary"]
    assert s["total"] == 40
    assert s["pass"] + s["revise"] + s["reject"] == 40
    assert s["pass"] >= 1 and s["revise"] >= 1, "40 张应覆盖多类建议"
    assert set(s["confidence"]) <= {"high", "medium", "low"}
    assert len(d["results"]) == 40
    ids = [r["review_id"] for r in d["results"]]
    assert len(set(ids)) == 40 and all(i.startswith("CP3-") for i in ids)
    for r in d["results"]:
        assert r["suggested_verdict"] in ("pass", "revise", "reject")
        assert r["confidence"] in ("high", "medium", "low")
        assert isinstance(r["score"], int) and r["reasons"]
        assert r["theme"] and r["angle_cn"]
    # 优先复核清单
    assert 1 <= len(s["priority_review"]) <= 10
    assert all(p["review_id"].startswith("CP3-") for p in s["priority_review"])
    # 主题/角度分布合计
    assert sum(t["total"] for t in s["by_theme"]) == 40
    assert sum(a["total"] for a in s["by_angle"]) == 40


# ---------------------------------------------------------------------------
# 3) 审核页数据回写（CP3 更新 + 第一轮不变 + 统计一致）
# ---------------------------------------------------------------------------
def test_review_data_updated_round1_unchanged():
    d = report()
    data = load(REVIEW_DATA)
    suggest = {r["review_id"]: r for r in d["results"]}
    cp3_items = [i for i in data["items"] if i.get("round") == "second"]
    assert len(cp3_items) == 40
    for it in cp3_items:
        rec = suggest[it["review_id"]]
        pre = it.get("precheck") or {}
        assert pre.get("suggested_verdict") == rec["suggested_verdict"], it["review_id"]
        assert pre.get("confidence") == rec["confidence"]
        assert pre.get("reasons")
    # 第一轮 60 条：review_id/建议/来源保持不变（36 pass + 24 revise）
    round1 = [i for i in data["items"] if i.get("round") == "first"]
    assert len(round1) == 60
    counts = Counter((i.get("precheck") or {}).get("suggested_verdict") for i in round1)
    assert counts.get("pass") == 36 and counts.get("revise") == 24 and counts.get("reject", 0) == 0
    assert all(i["review_id"].startswith("REV-") and i["source"] in ("AE", "AV") for i in round1)
    # stats.precheck 与全量条目统计一致
    all_counts = Counter((i.get("precheck") or {}).get("suggested_verdict") for i in data["items"])
    expected = {"pass": all_counts.get("pass", 0), "revise": all_counts.get("revise", 0),
                "reject": all_counts.get("reject", 0), "none": all_counts.get(None, 0)}
    assert data["stats"]["precheck"] == expected
    assert data["stats"]["total"] == 100 and data["stats"]["by_round"] == {"first": 60, "second": 40}
    assert d["data_update"]["updated_cp3"] == 40
    assert d["data_update"]["round1_unchanged"] is True


# ---------------------------------------------------------------------------
# 4) 不修改源数据 + 幂等
# ---------------------------------------------------------------------------
def test_no_mutation_and_idempotent():
    d = report()
    assert d.get("source_unchanged") is True
    src = d["source"]
    source_hashes = {
        "proposals": (src["proposals_sha256_before"], PROPOSALS),
        "learning_cards": (src["learning_cards_sha256_before"], CARDS),
        "checklist": (src["checklist_sha256_before"], CHECKLIST),
    }
    if archived(d):
        assert all(len(h) == 64 for h, _ in source_hashes.values())
    else:
        for h, path in source_hashes.values():
            assert h == sha(path)
    # 重复构建（不写盘）不得改动任何源数据，且分布一致。
    before = {name: sha(path) for name, path in (
        ("proposals", PROPOSALS), ("cards", CARDS), ("checklist", CHECKLIST),
        ("review_data", REVIEW_DATA))}
    again = cp3.build(write=False)
    again2 = cp3.build(write=False)
    after = {name: sha(path) for name, path in (
        ("proposals", PROPOSALS), ("cards", CARDS), ("checklist", CHECKLIST),
        ("review_data", REVIEW_DATA))}
    assert after == before, "只读构建不得改动源数据"
    # 幂等：连续两次只读构建结果一致；只有 live 源仍等于报告快照时才要求等于历史分布。
    assert again["summary"] == again2["summary"]
    if not archived(d):
        assert again["summary"]["pass"] == d["summary"]["pass"]
        assert again["summary"]["revise"] == d["summary"]["revise"]
        assert again["summary"]["reject"] == d["summary"]["reject"]


# ---------------------------------------------------------------------------
# 5) 报告 schema / MD
# ---------------------------------------------------------------------------
def test_report_schema_and_md():
    d = report()
    for key in ("schema_version", "date", "generated_at", "generator", "mode", "source",
                "policy", "summary", "results", "data_update", "disclaimer"):
        assert key in d, key
    assert d["mode"].startswith("rule_based_precheck")
    assert d["policy"]["reused_rules"].endswith("card_precheck.py")
    assert d["date"] == "20261101"
    md = REPORT_MD.read_text(encoding="utf-8")
    for marker in ("第二轮 40 候选机器预审建议", "建议分布", "按主题分布", "逐卡预审建议",
                   "一键采用机器预审建议", "投资建议"):
        assert marker in md, marker
