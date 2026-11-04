# -*- coding: utf-8 -*-
"""学习卡第三轮扩卡候选测试（task-126）。

覆盖：
  1) 124 卡 21 主题深度统计：卡数/单元数合计正确、缺口主题（<3 卡）识别；
  2) 候选：20-30 张、三角度、可追溯、pending_review、出处格式、与现有卡去重 + 批内去重；
  3) dry-run 预览：124 → 124+N（不执行）；学习卡 sha/卡数不变（sources_unchanged）；
  4) schema + portal JSON + MD 审核稿结构。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
S = REPO / "portal" / "static"
R = REPO / "research" / "reports"
CARDS = S / "kb_learning_cards.json"
UNITS = S / "kb_units_all.json"
PORTAL = S / "card_proposals_v4.json"
REPORT_JSON = R / "card_proposals_v4_20261102.json"
REPORT_MD = R / "card_proposals_v4_20261102.md"
NOISE = re.compile(r"[\s\u3000（）()【】\[\]《》「」『』“”\"'’·、，,。.：:；;？?！!—-]+")
SRC_RE = re.compile(r"《(.+?)》(.*?)p(\d+)-(\d+)")


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8-sig"))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def report() -> dict:
    return load(REPORT_JSON)


def archived(d: dict) -> bool:
    """报告生成后学习卡又扩卡 → 历史快照，按动态/归档口径校验。"""
    return d["sources"]["cards"]["sha256"] != sha(CARDS)


def norm(s) -> str:
    return NOISE.sub("", str(s or "")).lower()


# ---------------------------------------------------------------------------
# 1) 主题深度 / 缺口
# ---------------------------------------------------------------------------
def test_theme_depth_and_gaps():
    d = report()
    s = d["summary"]
    cards = load(CARDS)["cards"]
    units = load(UNITS)["units"]
    assert s["cards_total"] == d["preview"]["base_cards"] == 124   # 报告自身快照
    assert s["units_total"] == 516
    if not archived(d):
        assert s["cards_total"] == len(cards)
        assert s["units_total"] == len(units)
    assert s["themes_total"] == 21
    rows = d["theme_depth"]
    assert len(rows) == 21
    assert sum(r["cards"] for r in rows) == 124
    assert sum(r["units"] for r in rows) == 516
    # 缺口 = 卡数 <3 的主题
    gaps = [r["theme"] for r in rows if r["cards"] < 3]
    assert s["gap_themes_count"] == len(gaps) == 4
    assert sorted(s["gap_themes"]) == sorted(gaps)
    assert s["themes_depth_ok"] == 17
    assert all(r["cards"] >= 3 for r in rows if r["theme"] not in gaps)


# ---------------------------------------------------------------------------
# 2) 候选质量
# ---------------------------------------------------------------------------
def test_candidates_quality():
    d = report()
    cands = d["candidates"]
    assert 20 <= len(cands) <= 30
    assert len(d["summary"]["by_theme"]) >= 1
    ids = [c["card_id"] for c in cands]
    assert len(set(ids)) == len(ids) and all(i.startswith("CP4-20261102-") for i in ids)
    # 三角度
    angles = {c["angle_cn"] for c in cands}
    assert angles == {"概念定义", "关键事实", "影响传导"}
    assert d["summary"]["angles"] == {"概念定义": 8, "关键事实": 8, "影响传导": 8}
    # 全部可追溯 + 出处格式 + pending_review
    # 历史报告：去重基准是报告生成时的 124 卡快照；当前卡已扩卡 → 跳过“不得与当前卡重复”
    card_concepts = set()
    if not archived(d):
        card_concepts = {norm(c.get("concept")) for c in load(CARDS)["cards"]}
    cand_concepts = []
    for c in cands:
        assert c["status"] == "pending_review"
        assert c["traceable"] is True
        assert isinstance(c["page_start"], int) and isinstance(c["page_end"], int)
        assert SRC_RE.search(str(c["source_ref"]))
        assert c["evidence"] and c["q"] and c["a"]
        assert len(c["a"]) <= 300
        key = norm(c["concept"])
        assert key and key not in card_concepts, f"候选与现有卡重复：{c['concept']}"
        cand_concepts.append(key)
        assert c["review"]["checkboxes"] == {"pass": False, "modify": False, "reject": False}
    assert len(set(cand_concepts)) == len(cand_concepts)


# ---------------------------------------------------------------------------
# 3) dry-run 预览 / 不改学习卡
# ---------------------------------------------------------------------------
def test_dry_run_preview_and_no_mutation():
    d = report()
    pv = d["preview"]
    cards = load(CARDS)["cards"]
    assert pv["base_cards"] == d["summary"]["cards_total"] == 124
    assert pv["candidates"] == len(d["candidates"]) == 24
    assert pv["resulting_cards_if_all_merged"] == pv["base_cards"] + 24 == 148
    assert "dry-run" in pv["mode"] and ("未执行" in pv["mode"] or "不执行" in pv["mode"])
    assert d["source_unchanged"] is True
    if not archived(d):
        assert d["sources"]["cards"]["sha256"] == sha(CARDS)
        assert d["sources"]["units"]["sha256"] == sha(UNITS)
    # 缺口主题未补齐说明
    assert d["summary"]["gap_themes_unfilled"]
    assert any("未生成候选" in n for n in d["known_notes"])


# ---------------------------------------------------------------------------
# 4) schema + portal + MD
# ---------------------------------------------------------------------------
def test_schema_portal_markdown():
    d = report()
    for key in ("schema_version", "date", "generated_at", "mode", "sources", "policy",
                "summary", "preview", "theme_depth", "candidates", "known_notes",
                "instructions", "source_unchanged", "disclaimer"):
        assert key in d, key
    assert d["date"] == "20261102" and d["status"] == "pending_review" if "status" in d else True
    portal = load(PORTAL)
    assert len(portal["candidates"]) == len(d["candidates"]) == 24
    assert portal["summary"]["candidates"] == 24
    assert portal["status"] == "pending_review"
    md = REPORT_MD.read_text(encoding="utf-8")
    for marker in ("第三轮扩卡候选", "21 主题深度", "缺口主题与候选分组", "dry-run 预览",
                   "☐通过", "☐修改", "☐驳回"):
        assert marker in md, marker
