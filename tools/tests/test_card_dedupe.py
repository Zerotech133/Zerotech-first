# -*- coding: utf-8 -*-
"""「涨停密码」重复卡合并执行测试（task-105）。

覆盖：
  1) 决策：保留 index 5（信息量更大）、删除 index 7，理由与对比数据齐全；
  2) 去重后：卡数 105→104、「涨停密码」仅 1 张、无任何重复 concept、schema 字段完整；
  3) 其余卡不变：备份（105 张）去掉被删卡后与当前 104 张逐张一致；备份 sha 与报告一致；
  4) 报告/回滚/digest/HTTP：rollback 指向存在的备份；digest rc=0 且 sha 一致；HTTP 全 200。
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
REPORT_JSON = R / "card_dedupe_20261101.json"
REPORT_MD = R / "card_dedupe_20261101.md"
REQUIRED = ("q", "a", "concept", "source", "topic", "evidence")


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8-sig"))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def report() -> dict:
    return load(REPORT_JSON)


# ---------------------------------------------------------------------------
# 1) 保留决策
# ---------------------------------------------------------------------------
def test_keep_decision():
    rep = report()
    assert rep["applied"] is True and rep["concept"] == "涨停密码"
    dec = rep["decision"]
    assert dec["keep_index"] == 5 and dec["drop_index"] == 7
    keep, drop = dec["keep_quality"], dec["drop_quality"]
    assert keep["score"] > drop["score"], "保留卡信息量应更大"
    assert keep["answer_len"] > drop["answer_len"] and keep["evidence_len"] > drop["evidence_len"]
    assert len(dec["reasons"]) >= 2 and "信息量" in dec["rule"]
    assert len(rep["candidates"]) == 2
    assert rep["candidates"][0]["card"]["a"] and rep["candidates"][1]["card"]["a"]


# ---------------------------------------------------------------------------
# 2) 去重后卡数 / 无重复 / schema
# ---------------------------------------------------------------------------
def test_cards_after_dedupe():
    rep = report()
    cards = load(CARDS)["cards"]
    assert len(cards) == 104 == rep["after"]["cards"]
    assert rep["before"]["cards"] == 105
    concepts = [str(c.get("concept") or "") for c in cards]
    dups = [(k, v) for k, v in Counter(concepts).items() if v > 1]
    assert dups == [], f"去重后仍有重复 concept：{dups}"
    assert Counter(concepts)["涨停密码"] == 1
    # 保留的是信息量更大的卡（答案 63 字、含定义）
    kp = [c for c in cards if c.get("concept") == "涨停密码"][0]
    assert len(kp["a"]) == rep["decision"]["keep_quality"]["answer_len"]
    assert "构成股票涨停或走牛的基本因子组合" in kp["a"]
    # schema
    for i, c in enumerate(cards):
        for key in REQUIRED:
            assert str(c.get(key) or "").strip(), f"cards[{i}] 缺 {key}"


# ---------------------------------------------------------------------------
# 3) 其余卡逐张不变 + 备份
# ---------------------------------------------------------------------------
def test_others_unchanged_and_backup():
    rep = report()
    cards = load(CARDS)["cards"]
    bak = Path(rep["backups"]["pre_dedupe"]["path"])
    assert bak.is_file() and bak.parent == R / "backups"
    bak_cards = load(bak)["cards"]
    assert len(bak_cards) == 105
    assert sha(bak) == rep["backups"]["pre_dedupe"]["sha256"] == rep["before"]["sha256"]
    drop = rep["decision"]["drop_index"]
    remaining = [c for i, c in enumerate(bak_cards) if i != drop]
    assert len(remaining) == len(cards) == 104
    assert all(a == b for a, b in zip(remaining, cards)), "除被删卡外其余卡应逐张不变"
    assert rep["validation"]["others_unchanged"] is True
    assert rep["validation"]["ok"] is True
    assert rep["validation"]["dup_concepts_after"] == []
    assert rep["validation"]["atomic_write_report"]["atomic"] is True


# ---------------------------------------------------------------------------
# 4) 回滚 / digest / HTTP / 报告
# ---------------------------------------------------------------------------
def test_report_rollback_digest_http():
    rep = report()
    for key in ("schema_version", "generated_at", "candidates", "decision", "before", "after",
                "validation", "backups", "digest", "http", "rollback", "note"):
        assert key in rep, key
    assert "copy /Y" in rep["rollback"]["cards"]
    assert str(Path(rep["backups"]["pre_dedupe"]["path"])) in rep["rollback"]["cards"]
    assert rep["digest"]["rc"] == 0
    assert DIGEST.is_file() and rep["digest"]["sha256"] == sha(DIGEST)
    for name in ("kb_learning_cards", "kb_learning_digest", "kb_reports_page"):
        assert rep["http"][name]["status"] == 200, name
    assert rep["after"]["sha256"] == sha(CARDS)
    md = REPORT_MD.read_text(encoding="utf-8")
    for marker in ("重复卡合并报告", "两张重复卡对比", "决策", "前后数量与校验", "回滚"):
        assert marker in md, marker
