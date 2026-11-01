# -*- coding: utf-8 -*-
"""2 张 CP3 卡概念对齐修复测试（task-121）。

覆盖：
  1) 报告：2 张修复记录、覆盖 0%→100%、规则=答案前缀概念锚点（不编造）、备份存在；
  2) 学习卡现状：124 张不变、2 张答案以概念前缀且覆盖 ≥34%、无重复 concept、schema 完整；
  3) 其余不变：122 张与备份逐张一致；备份 124 张且 sha 一致；
  4) digest/HTTP/审计报告更新：rc=0、HTTP 全 200、审计报告登记「已修复」；MD 结构。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
S = REPO / "portal" / "static"
R = REPO / "research" / "reports"
CARDS = S / "kb_learning_cards.json"
DIGEST = S / "kb_learning_digest.json"
AUDIT_JSON = R / "card_quality_audit_124_20261102.json"
REPORT_JSON = R / "card_concept_fix_20261102.json"
REPORT_MD = R / "card_concept_fix_20261102.md"
CQA = REPO / "tools" / "kb_corpus" / "card_quality_audit.py"
TARGETS = [104, 105]
REQUIRED = ("q", "a", "concept", "source", "topic", "evidence")

_spec = importlib.util.spec_from_file_location("cqa_fix_test", CQA)
cqa = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = cqa
_spec.loader.exec_module(cqa)  # type: ignore[union-attr]


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8-sig"))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def report() -> dict:
    return load(REPORT_JSON)


# ---------------------------------------------------------------------------
# 1) 报告 / 修复记录
# ---------------------------------------------------------------------------
def test_report_fixes():
    d = report()
    for key in ("schema_version", "generated_at", "rule", "fixes", "before", "after",
                "validation", "backups", "digest", "http", "audit_report_update", "rollback"):
        assert key in d, key
    assert d["applied"] is True and "概念锚点" in d["rule"] and "不编造" in d["rule"]
    assert len(d["fixes"]) == 2
    for f in d["fixes"]:
        assert f["card_index"] in TARGETS
        assert f["coverage_before"] < 0.34 and f["coverage_after"] >= 0.34
        assert f["after"]["a"].startswith(str(f["concept"]) + "：")
        assert f["after"]["a"].endswith(f["before"]["a"]), "仅前缀，不删改原文"
        assert f["after"]["answer_len"] > f["before"]["answer_len"]
    bak = Path(d["backups"]["pre_fix"]["path"])
    assert bak.is_file() and bak.parent == R / "backups"
    assert len(load(bak)["cards"]) == 124
    assert sha(bak) == d["backups"]["pre_fix"]["sha256"] == d["before"]["sha256"]


# ---------------------------------------------------------------------------
# 2) 学习卡现状
# ---------------------------------------------------------------------------
def test_cards_fixed_and_valid():
    d = report()
    cards = load(CARDS)["cards"]
    assert len(cards) == 124 == d["after"]["cards"]
    for idx in TARGETS:
        c = cards[idx]
        concept = str(c.get("concept") or "")
        assert str(c.get("a") or "").startswith(concept + "：")
        cov = cqa.containment(concept, str(c.get("a") or "") + " " + str(c.get("evidence") or ""))
        assert cov >= 0.34, (idx, cov)
    # schema
    for i, c in enumerate(cards):
        for key in REQUIRED:
            assert str(c.get(key) or "").strip(), f"cards[{i}] 缺 {key}"
    dups = [(k, v) for k, v in Counter(str(c.get("concept") or "") for c in cards).items() if v > 1]
    assert dups == []
    assert d["validation"]["ok"] is True
    assert d["validation"]["others_unchanged"] is True


# ---------------------------------------------------------------------------
# 3) 其余 122 张不变
# ---------------------------------------------------------------------------
def test_others_unchanged():
    d = report()
    cards = load(CARDS)["cards"]
    bak_cards = load(Path(d["backups"]["pre_fix"]["path"]))["cards"]
    assert len(bak_cards) == len(cards) == 124
    for i in range(124):
        if i in TARGETS:
            continue
        assert cards[i] == bak_cards[i], f"cards[{i}] 不应改变"
    # 目标卡仅 a 变化
    for idx in TARGETS:
        changed = {k for k in cards[idx] if cards[idx][k] != bak_cards[idx][k]}
        assert changed <= {"a"}, changed
    assert d["validation"]["atomic_write"]["atomic"] is True


# ---------------------------------------------------------------------------
# 4) digest / HTTP / 审计报告
# ---------------------------------------------------------------------------
def test_digest_http_audit_update():
    d = report()
    assert d["digest"]["rc"] == 0
    assert DIGEST.is_file() and d["digest"]["sha256"] == sha(DIGEST)
    for name in ("kb_learning_cards", "kb_learning_digest", "kb_reports_page"):
        assert d["http"][name]["status"] == 200, name
    assert d["audit_report_update"]["updated"] is True
    audit = load(AUDIT_JSON)
    assert audit["concept_fix"]["status"].startswith("fixed")
    assert len(audit["concept_fix"]["cards"]) == 2
    assert any("修复" in n for n in audit.get("known_notes") or [])
    md = REPORT_MD.read_text(encoding="utf-8")
    for marker in ("概念对齐修复报告", "修复前后对比", "校验", "回滚", "124 张不变"):
        assert marker in md, marker
