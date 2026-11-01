# -*- coding: utf-8 -*-
"""学习卡第二轮 revise 落地执行测试（task-115）。

覆盖：
  1) 报告/真实数字：104 → 124（+20），drop 25 为 no-op；operations 20 add + 25 drop；
  2) 去重后：124 张、无重复 concept、schema 完整；20 张新增卡均在库；
  3) 其余不变：前 104 张与备份逐张一致；25 张 drop 的 concept 仍各 1 张（未删现有卡）；
  4) 备份/回滚/digest/HTTP：备份存在且 sha 一致、回滚指向备份、digest rc=0、HTTP 全 200；
  5) 源数据一致：revisions sha 匹配、报告 MD 结构。
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
S = REPO / "portal" / "static"
R = REPO / "research" / "reports"
CARDS = S / "kb_learning_cards.json"
DIGEST = S / "kb_learning_digest.json"
REVISIONS = R / "card_revisions_round2_20261101.json"
REPORT_JSON = R / "card_round2rev_apply_20261102.json"
REPORT_MD = R / "card_round2rev_apply_20261102.md"
REQUIRED = ("q", "a", "concept", "source", "topic", "evidence")


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8-sig"))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def report() -> dict:
    return load(REPORT_JSON)


# ---------------------------------------------------------------------------
# 1) 报告 / 真实数字
# ---------------------------------------------------------------------------
def test_report_actual_numbers():
    rep = report()
    for key in ("schema_version", "generated_at", "sources", "dry_run", "operations",
                "before", "after", "validation", "backups", "digest", "http", "rollback",
                "actual_numbers", "safety"):
        assert key in rep, key
    assert rep["applied"] is True
    nums = rep["actual_numbers"]
    assert nums["before"] == 104 and nums["added"] == 20 and nums["after"] == 124 and nums["drop_noop"] == 25
    assert len(rep["operations"]["add"]) == 20
    assert len(rep["operations"]["drop_noop"]) == 25
    v = rep["validation"]
    assert v["ok"] is True and v["count_before"] == 104 and v["count_after"] == 124
    assert v["dup_concepts"] == [] and v["schema_errors"] == []
    assert v["others_unchanged"] is True
    assert rep["safety"]["real_drop_deletions"] == 0


# ---------------------------------------------------------------------------
# 2) 卡数 / 无重复 / schema / 新增卡
# ---------------------------------------------------------------------------
def test_cards_after_round2rev():
    rep = report()
    cards = load(CARDS)["cards"]
    assert len(cards) == 124
    dups = [(k, v) for k, v in Counter(c.get("concept") for c in cards).items() if v > 1]
    assert dups == []
    for i, c in enumerate(cards):
        for key in REQUIRED:
            assert str(c.get(key) or "").strip(), f"cards[{i}] 缺 {key}"
    added = rep["operations"]["add"]
    concepts = {c.get("concept") for c in cards}
    assert len(added) == 20
    assert all(a["concept"] in concepts for a in added)
    assert [c.get("concept") for c in cards[104:]] == [a["concept"] for a in added]


# ---------------------------------------------------------------------------
# 3) 其余不变 / drop 不删现有卡
# ---------------------------------------------------------------------------
def test_others_unchanged_and_drop_semantics():
    rep = report()
    cards = load(CARDS)["cards"]
    bak = Path(rep["backups"]["pre_apply"]["path"])
    assert bak.is_file() and bak.parent == R / "backups"
    bak_cards = load(bak)["cards"]
    assert len(bak_cards) == 104
    assert sha(bak) == rep["backups"]["pre_apply"]["sha256"]
    assert all(a == b for a, b in zip(bak_cards, cards[:104])), "前 104 张应逐张不变"
    # 25 张 drop 的 concept 在现有 104 卡中各保留 1 张（未删）
    drop_concepts = [op["concept"] for op in rep["operations"]["drop_noop"]]
    assert len(drop_concepts) == 25
    counter = Counter(c.get("concept") for c in cards)
    for concept in set(drop_concepts):
        assert counter.get(concept) == 1, f"drop 不应删除现有卡：{concept}"
    # 被删卡数 0：备份中每张 concept 在 after(124) 中仍存在
    after_concepts = {c.get("concept") for c in cards}
    assert all(c.get("concept") in after_concepts for c in bak_cards)


# ---------------------------------------------------------------------------
# 4) 备份 / 回滚 / digest / HTTP
# ---------------------------------------------------------------------------
def test_backup_rollback_digest_http():
    rep = report()
    bak = rep["backups"]["pre_apply"]
    assert Path(bak["path"]).is_file()
    assert sha(Path(bak["path"])) == bak["sha256"]
    assert "copy /Y" in rep["rollback"]["cards"]
    assert str(Path(bak["path"])) in rep["rollback"]["cards"]
    assert rep["digest"]["rc"] == 0
    assert DIGEST.is_file() and rep["digest"]["sha256"] == sha(DIGEST)
    for name in ("kb_learning_cards", "kb_learning_digest", "kb_reports_page"):
        assert rep["http"][name]["status"] == 200, name
    assert rep["after"]["sha256"] == sha(CARDS)


# ---------------------------------------------------------------------------
# 5) 源数据一致 + MD
# ---------------------------------------------------------------------------
def test_sources_and_markdown():
    rep = report()
    assert rep["sources"]["revisions"]["sha256"] == sha(REVISIONS)
    assert load(REVISIONS)["summary"]["actions"] == {"merge_fix": 20, "drop": 25, "keep_revise": 0}
    md = REPORT_MD.read_text(encoding="utf-8")
    for marker in ("第二轮 revise 落地报告", "执行结果（真实数字）", "新增卡片（20 张）",
                   "drop 说明", "回滚"):
        assert marker in md, marker
