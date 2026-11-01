# -*- coding: utf-8 -*-
"""第三轮 24 候选落地测试（task-127）。

覆盖：
  1) 预审：24 张、建议分布 6/17/1、置信/主题合计自洽；
  2) decisions：24 条、与预审逐卡一致、schema 兼容 decide（verdict 映射）；
  3) 落地结果：学习卡 124→130（+approved 6）、无重复 concept、schema 完整、前 124 张逐张不变；
  4) 仅 approved 合并：17 revise + 1 reject 的 concept 均未进入学习卡；新增 6 张全部入库；
  5) 备份/回滚/digest/HTTP：备份存在且 sha 一致、digest rc=0、HTTP 全 200、报告 MD 结构。
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
PORTAL_V4 = S / "card_proposals_v4.json"
PRECHECK = R / "card_precheck_v4_20261102.json"
DECISIONS = R / "card_decisions_round3_20261102.json"
REPORT_JSON = R / "card_round3_apply_20261102.json"
REPORT_MD = R / "card_round3_apply_20261102.md"
REQUIRED = ("q", "a", "concept", "source", "topic", "evidence")


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8-sig"))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def report() -> dict:
    return load(REPORT_JSON)


# ---------------------------------------------------------------------------
# 1) 预审
# ---------------------------------------------------------------------------
def test_precheck():
    d = load(PRECHECK)
    s = d["summary"]
    assert s["total"] == 24 == len(d["entries"])
    assert (s["pass"], s["revise"], s["reject"]) == (6, 17, 1)
    assert sum(s["confidence"].values()) == 24
    assert all(e["suggested_verdict"] in ("pass", "revise", "reject") for e in d["entries"])
    assert sum(t["total"] for t in s["by_theme"]) == 24
    assert d["sources"]["proposals"]["sha256"] == sha(PORTAL_V4)


# ---------------------------------------------------------------------------
# 2) decisions
# ---------------------------------------------------------------------------
def test_decisions_mapping():
    pre = {e["review_id"]: e for e in load(PRECHECK)["entries"]}
    dec = load(DECISIONS)
    assert len(dec["decisions"]) == 24
    assert dec["summary"] == {"total": 24, "pass": 6, "revise": 17, "reject": 1}
    mapping = {"pass": "pass", "revise": "revise", "reject": "reject"}
    for rid, v in dec["decisions"].items():
        verdict = v["verdict"] if isinstance(v, dict) else v
        assert verdict == mapping[pre[rid]["suggested_verdict"]], rid
        assert v["notes"] if isinstance(v, dict) else True
    assert dec["source"]["proposals_sha256"] == sha(PORTAL_V4)


# ---------------------------------------------------------------------------
# 3) 落地结果
# ---------------------------------------------------------------------------
def test_apply_result():
    rep = report()
    cards = load(CARDS)["cards"]
    nums = rep["actual_numbers"]
    assert nums["before"] == 124 and nums["approved"] == 6 and nums["after"] == 130
    assert nums["added"] == 6 and nums["revise_not_merged"] == 17 and nums["reject_not_merged"] == 1
    assert len(cards) == 130
    concepts = [str(c.get("concept") or "") for c in cards]
    dups = [(k, v) for k, v in Counter(concepts).items() if v > 1]
    assert dups == []
    for i, c in enumerate(cards):
        for k in REQUIRED:
            assert str(c.get(k) or "").strip(), f"cards[{i}] 缺 {k}"
    v = rep["validation"]
    assert v["ok"] is True and v["count_after"] == 130
    assert v["others_unchanged"] is True
    assert rep["dry_run"]["approved"] == 6 and rep["dry_run"]["ok"] is True


# ---------------------------------------------------------------------------
# 4) 仅 approved 合并 / 其余不变
# ---------------------------------------------------------------------------
def test_only_approved_added():
    rep = report()
    cards = load(CARDS)["cards"]
    bak = Path(rep["backups"]["pre_apply"]["path"])
    assert bak.is_file() and bak.parent == R / "backups"
    bak_cards = load(bak)["cards"]
    assert len(bak_cards) == 124
    assert sha(bak) == rep["backups"]["pre_apply"]["sha256"]
    assert all(a == b for a, b in zip(bak_cards, cards[:124])), "前 124 张应逐张不变"
    added = rep["after"]["added_concepts"]
    assert len(added) == 6
    final_concepts = {c.get("concept") for c in cards}
    assert all(c in final_concepts for c in added)
    # revise/reject 的 concept 不应被合并
    pre = {e["review_id"]: e for e in load(PRECHECK)["entries"]}
    not_merged = [e["concept"] for rid, e in pre.items() if e["suggested_verdict"] in ("revise", "reject")]
    assert len(not_merged) == 18
    assert not (set(not_merged) & set(added)), "revise/reject 不应合并"


# ---------------------------------------------------------------------------
# 5) 备份 / digest / HTTP / MD
# ---------------------------------------------------------------------------
def test_backup_digest_http_md():
    rep = report()
    mb = rep["backups"]["merge"]
    assert mb and Path(mb["path"]).is_file() and sha(Path(mb["path"])) == mb["sha256"]
    assert "copy /Y" in rep["rollback"]["cards"]
    assert rep["digest"]["rc"] == 0
    assert DIGEST.is_file() and rep["digest"]["sha256"] == sha(DIGEST)
    for name in ("kb_learning_cards", "kb_learning_digest", "kb_reports_page"):
        assert rep["http"][name]["status"] == 200, name
    md = REPORT_MD.read_text(encoding="utf-8")
    for marker in ("第三轮 24 候选落地报告", "预审与决策分布", "执行结果（真实数字）", "回滚"):
        assert marker in md, marker
    assert "124 → 130" in md or ("124" in md and "130" in md)
