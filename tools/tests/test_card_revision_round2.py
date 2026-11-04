# -*- coding: utf-8 -*-
"""第二轮 45 张 revise 修订建议稿测试（task-111）。

覆盖：
  1) 覆盖与来源：45 张 revise（AE8/AV16/CP3 21）与 decisions 一一对应；
  2) schema/动作：merge_fix/drop/keep_revise 分布合计 45；每卡有理由/审核位；merge_fix 带修订 after；
  3) 去重与对齐：drop 理由含「现有学习卡」或「批内同 concept」；对齐/出处/长度分析函数可复现；
  4) dry-run 预览：base 104、merge_fix 新增 20、resulting 124；未执行（不改学习卡/清单）；
  5) 只读安全：sources_unchanged=True、cards/checklist sha 与当前一致；MD 结构。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
R = REPO / "research" / "reports"
S = REPO / "portal" / "static"
DECISIONS = R / "card_review_decisions_suggested_round2_20261101.json"
V2 = R / "card_review_checklist_20261101_v2.json"
CARDS = S / "kb_learning_cards.json"
BACKUP_104 = R / "backups" / "kb_learning_cards.20261102_003714.pre_round2rev.bak.json"
REPORT_JSON = R / "card_revisions_round2_20261101.json"
REPORT_MD = R / "card_revisions_round2_20261101.md"
SCRIPT = REPO / "tools" / "kb_corpus" / "card_revision_round2.py"

_spec = importlib.util.spec_from_file_location("crr2_test", SCRIPT)
crr2 = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = crr2
_spec.loader.exec_module(crr2)  # type: ignore[union-attr]


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8-sig"))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def report() -> dict:
    return load(REPORT_JSON)


def archived(d: dict) -> bool:
    """报告生成后学习卡已继续扩卡，按历史归档口径校验。"""
    return d["sources"]["cards"]["sha256"] != sha(CARDS)


# ---------------------------------------------------------------------------
# 1) 覆盖与来源
# ---------------------------------------------------------------------------
def test_coverage_and_sources():
    d = report()
    revs = d["revisions"]
    assert len(revs) == 45
    dec = load(DECISIONS)["decisions"]
    expected = {rid for rid, v in dec.items() if (v.get("verdict") if isinstance(v, dict) else v) == "revise"}
    assert {r["review_id"] for r in revs} == expected
    by_src = Counter(r["source"] for r in revs)
    assert by_src == {"AE": 8, "AV": 16, "CP3": 21}
    assert d["policy"]["target"].startswith("decisions round2")
    assert d["mode"].startswith("suggestion_only")


# ---------------------------------------------------------------------------
# 2) schema / 动作分布
# ---------------------------------------------------------------------------
def test_schema_and_action_distribution():
    d = report()
    s = d["summary"]
    assert s["total"] == 45
    assert sum(s["actions"].values()) == 45
    assert set(s["actions"]) == {"merge_fix", "drop", "keep_revise"}
    assert s["actions"]["merge_fix"] + s["actions"]["drop"] + s["actions"]["keep_revise"] == 45
    for r in d["revisions"]:
        for key in ("revision_id", "review_id", "source", "theme", "concept", "original",
                    "analysis", "actions", "final_action", "reasons", "review", "traceable"):
            assert key in r, key
        assert r["final_action"] in crr2.ACTIONS
        assert isinstance(r["reasons"], list) and r["reasons"]
        assert r["review"]["checkboxes"] == {"merge_fix": False, "drop": False, "keep_revise": False}
        if r["final_action"] == "merge_fix":
            fixes = [a for a in r["actions"] if a["action"] == "merge_fix"]
            assert fixes and all("after" in a for a in fixes), r["revision_id"]
    # 修订类型统计
    assert s["fix_types"].get("q/a", 0) >= 1 and s["fix_types"].get("source_ref", 0) >= 1


# ---------------------------------------------------------------------------
# 3) 去重与对齐分析
# ---------------------------------------------------------------------------
def test_dedupe_and_alignment():
    d = report()
    drops = [r for r in d["revisions"] if r["final_action"] == "drop"]
    assert drops, "应有 drop"
    for r in drops:
        assert any(("现有学习卡" in x or "批内同 concept" in x) for x in r["reasons"]), r["review_id"]
    # merge_fix 卡：概念覆盖低或出处/长度需修（分析字段可复现）
    cr = crr2.load_revision_tool()
    v2 = {e["review_id"]: e for e in load(V2)["entries"]}
    checked = 0
    for r in d["revisions"]:
        if r["final_action"] != "merge_fix":
            continue
        e = v2[r["review_id"]]
        align = cr.analyze_alignment(e)
        assert r["analysis"]["alignment"]["coverage"] == align["coverage"]
        checked += 1
    assert checked >= 1
    # 出处规范化建议：有 source_ref merge_fix 的卡 after 形如《…》…pX-Y
    src_fixes = [a for r in d["revisions"] for a in r["actions"]
                 if a["action"] == "merge_fix" and a["field"] == "source_ref"]
    assert src_fixes and all("《" in str(a["after"]) and "p" in str(a["after"]) for a in src_fixes)


# ---------------------------------------------------------------------------
# 4) dry-run 预览
# ---------------------------------------------------------------------------
def test_dry_run_preview():
    d = report()
    pv = d["preview"]
    cards = load(CARDS)["cards"]
    assert pv["base_cards"] == 104
    assert pv["merge_fix_count"] == d["summary"]["actions"]["merge_fix"] == 20
    assert pv["merge_fix_new_cards"] == 20
    assert pv["resulting_cards_if_all_merge_fix"] == 104 + 20 == 124
    assert "dry-run" in pv["mode"] and ("未执行" in pv["mode"] or "不执行" in pv["mode"])
    assert pv["drop_count"] == d["summary"]["actions"]["drop"] == 25
    if archived(d):
        bak = load(BACKUP_104)["cards"]
        assert len(bak) == 104
        assert cards[:104] == bak, "历史 base 的 104 张应仍逐张不变"
        assert len(cards) > 104, "预览不得回退已扩卡的学习卡"
    else:
        assert len(cards) == 104, "预览不得改学习卡"


# ---------------------------------------------------------------------------
# 5) 只读安全 + MD
# ---------------------------------------------------------------------------
def test_read_only_and_markdown():
    d = report()
    assert d["sources_unchanged"] is True
    if archived(d):
        assert d["sources"]["cards"]["sha256"] == sha(BACKUP_104)
        assert len(load(CARDS)["cards"]) > 104
    else:
        assert d["sources"]["cards"]["sha256"] == sha(CARDS)
    assert d["sources"]["checklist_v2"]["sha256"] == sha(V2)
    assert d["sources"]["decisions"]["sha256"] == sha(DECISIONS)
    assert len(load(CARDS)["cards"]) >= 104
    md = REPORT_MD.read_text(encoding="utf-8")
    for marker in ("第二轮 45 张 revise 修订建议稿", "动作分布", "dry-run 预览", "逐卡修订建议",
                   "☐ 采纳修订", "☐ 合并/删除", "☐ 保留待人工"):
        assert marker in md, marker
