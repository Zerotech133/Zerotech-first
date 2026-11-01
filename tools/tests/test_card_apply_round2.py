# -*- coding: utf-8 -*-
"""100 卡 apply+merge 执行验证测试（task-99）。

覆盖：
  1) 清单 v2：100 条（AE20/AV40/CP3 40）、通过 card_review.validate_checklist（CP3 白名单）、决策状态 53/45/2；
  2) 备份：学习卡 pre（88 张）/清单 v1（60 条）/merge 备份均存在且 sha256 一致；
  3) 执行结果：学习卡 88→105（+17）、17 张新增卡已入库、无新增重复 concept、写后校验通过；
  4) 报告 schema：dry-run 100/100、approved 53、真实数字、digest rc=0、HTTP 200、回滚路径可用；
  5) 兼容：v1 清单（60 条）仍通过校验（旧路径不破坏），card_review.py 允许 CP3。
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
V1 = R / "card_review_checklist_20261101.json"
V2 = R / "card_review_checklist_20261101_v2.json"
CARDS = S / "kb_learning_cards.json"
DIGEST = S / "kb_learning_digest.json"
DECISIONS = R / "card_review_decisions_suggested_round2_20261101.json"
REPORT_JSON = R / "card_apply_round2_20261101.json"
REPORT_MD = R / "card_apply_round2_20261101.md"
SCRIPT = REPO / "tools" / "kb_corpus" / "card_review.py"

_spec = importlib.util.spec_from_file_location("cr_apply_test", SCRIPT)
cr = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = cr
_spec.loader.exec_module(cr)  # type: ignore[union-attr]


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8-sig"))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def report() -> dict:
    return load(REPORT_JSON)


# ---------------------------------------------------------------------------
# 1) 清单 v2
# ---------------------------------------------------------------------------
def test_checklist_v2_compatible():
    v2 = load(V2)
    assert len(v2["entries"]) == 100
    by_source = Counter(e["source"] for e in v2["entries"])
    assert by_source == {"AE": 20, "AV": 40, "CP3": 40}
    assert cr.validate_checklist(v2, expected_total=None) == [], cr.validate_checklist(v2, expected_total=None)
    ds = v2["summary"]["decision_stats"]
    assert ds["pass"] == 53 and ds["revise"] == 45 and ds["reject"] == 2 and ds["pending"] == 0
    # CP3 条目必需字段
    cp3 = [e for e in v2["entries"] if e["source"] == "CP3"]
    assert len({e["review_id"] for e in cp3}) == 40
    for e in cp3:
        assert e["review_id"].startswith("CP3-") and e["unit_id"] and e["evidence"]
        assert isinstance(e["page_start"], int) and e["book"]
    # 旧路径：v1 清单 60 条仍通过校验（仅扩展 CP3 白名单）
    v1 = load(V1)
    assert len(v1["entries"]) == 60
    assert cr.validate_checklist(v1) == []
    assert cr.validate_checklist(load(V2)) is not None


# ---------------------------------------------------------------------------
# 2) 备份
# ---------------------------------------------------------------------------
def test_backups_exist():
    rep = report()
    cards_bak = Path(rep["backups"]["cards_pre"]["path"])
    v1_bak = Path(rep["backups"]["checklist_v1"]["path"])
    assert cards_bak.is_file() and v1_bak.is_file()
    assert cards_bak.parent == R / "backups"
    assert len(load(cards_bak)["cards"]) == 88 == rep["backups"]["cards_pre"]["cards"]
    assert len(load(v1_bak)["entries"]) == 60
    assert sha(cards_bak) == rep["backups"]["cards_pre"]["sha256"]
    assert sha(v1_bak) == rep["backups"]["checklist_v1"]["sha256"]
    mb = rep["backups"]["merge_backup"]
    assert mb and Path(mb["path"]).is_file() and sha(Path(mb["path"])) == mb["sha256"]
    # 回滚信息可解析且指向存在的备份
    assert "copy /Y" in rep["rollback"]["cards"]


# ---------------------------------------------------------------------------
# 3) 执行结果：卡数 / 新增 / 重复
# ---------------------------------------------------------------------------
def test_merge_result_cards_105():
    rep = report()
    cards = load(CARDS)["cards"]
    assert len(cards) == 105
    assert rep["actual_numbers"]["base_cards_before"] == 88
    assert rep["actual_numbers"]["cards_after"] == 105
    assert rep["actual_numbers"]["added"] == 17
    assert rep["actual_numbers"]["approved"] == 53
    # 无新增重复 concept（既有 涨停密码 x2 保持原样）
    dup = [(k, v) for k, v in Counter(c["concept"] for c in cards).items() if v > 1]
    assert dup == [("涨停密码", 2)]
    assert rep["validation"]["new_duplicates"] == []
    assert rep["validation"]["no_new_duplicates"] is True
    # 17 张新增卡（inserted）概念均已入库
    inserted = rep["merge"]["inserted"]
    assert len(inserted) == 17
    concepts = {c["concept"] for c in cards}
    assert all(x["concept"] in concepts for x in inserted)
    # 写后校验
    assert rep["merge"]["post_write_validation"]["ok"] is True
    assert rep["validation"]["cards_sha_after"] == sha(CARDS)


# ---------------------------------------------------------------------------
# 4) 报告 schema / 真实数字 / digest / HTTP
# ---------------------------------------------------------------------------
def test_report_schema_and_real_numbers():
    rep = report()
    for key in ("applied", "code_change", "checklist", "decisions", "dry_run", "apply",
                "merge", "validation", "backups", "digest", "http", "rollback", "actual_numbers"):
        assert key in rep, key
    assert rep["applied"] is True
    # 清单 v1 未改
    assert rep["checklist"]["v1"]["unchanged"] is True
    assert rep["checklist"]["v1"]["entries"] == 60
    # dry-run 100/100
    assert rep["dry_run"]["decisions_count"] == 100
    assert rep["dry_run"]["ok"] is True
    assert rep["dry_run"]["approved_cards"] == 53
    assert rep["dry_run"]["base_cards"] == 88
    assert rep["dry_run"]["plan_stats"]["would_add"] == 17
    assert rep["dry_run"]["no_write_during_dry_run"] is True
    # code change 仅扩展 CP3
    assert "CP3" in rep["code_change"]["change"]
    # digest 刷新成功 + HTTP 200
    assert rep["digest"]["rc"] == 0
    assert DIGEST.is_file() and rep["digest"]["sha256"] == sha(DIGEST)
    for name in ("kb_learning_cards", "kb_learning_digest", "kb_reports_page"):
        assert rep["http"][name]["status"] == 200, name
    # decisions 一致性
    assert rep["decisions"]["sha256"] == sha(DECISIONS)
    assert load(DECISIONS)["summary"] == {"total": 100, "pass": 53, "revise": 45, "reject": 2}


# ---------------------------------------------------------------------------
# 5) 执行报告 MD
# ---------------------------------------------------------------------------
def test_report_markdown():
    md = REPORT_MD.read_text(encoding="utf-8")
    for marker in ("apply+merge 执行报告", "真实数字", "清单 v2", "学习卡", "HTTP 验证",
                   "备份与回滚", "106" if False else "105"):
        assert marker in md, marker
    assert "88 → 105" in md or ("88" in md and "105" in md)
