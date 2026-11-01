# -*- coding: utf-8 -*-
"""学习卡修订落地（card_revision_apply）回归测试。

覆盖：
  1) revised_card：merge_fix 的 q/a/source_ref after 正确合成学习卡（topic 映射/evidence 保留）；
  2) build_plan：merge_fix 新增 vs 替换（concept 匹配）、drop 概念已存在→保留不删、drop 未合并→no-op；
  3) validate_result：6 字段 schema / 删卡/丢 concept / 新增重复 concept 均能拦截；
  4) dry-run 不写盘（cards/备份均不变）；
  5) --apply（tmp）：备份文件生成、写盘、写后校验、digest 刷新（mock）、二次 apply 幂等（数量不变）。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "kb_corpus" / "card_revision_apply.py"
REVISIONS = REPO / "research" / "reports" / "card_revisions_20261101.json"
CARDS = REPO / "portal" / "static" / "kb_learning_cards.json"

_spec = importlib.util.spec_from_file_location("card_revision_apply_tool", SCRIPT)
cra = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cra)  # type: ignore[union-attr]


def sha_text(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def card(concept: str, *, q: str | None = None, a: str | None = None,
         topic: str = "金融/资本市场") -> dict:
    return {"q": q or f"什么是「{concept}」？", "a": a or f"{concept}的旧答案",
            "concept": concept, "source": "《rp_A》 p1-2", "topic": topic,
            "evidence": f"{concept}旧证据"}


def revision(rid: str, concept: str, action: str, *, q=None, a=None, source=None,
             theme: str = "金融/地产") -> dict:
    actions = []
    if q or a:
        actions.append({"action": "merge_fix", "field": "q/a",
                        "before": {"q": "旧", "a": "旧答案"},
                        "after": {"q": q or f"「{concept}」是什么？", "a": a or f"{concept}的新答案"}})
    if source:
        actions.append({"action": "merge_fix", "field": "source_ref",
                        "before": "旧出处", "after": source})
    return {"revision_id": rid, "review_id": f"REV-{rid}", "concept": concept,
            "theme": theme, "original": {"question": f"什么是「{concept}」？",
                                          "answer": f"{concept}旧答案", "source_ref": "旧出处",
                                          "evidence": f"{concept}证据"},
            "actions": actions, "final_action": action, "reasons": ["测试"]}


def revisions_payload(rows: list[dict]) -> dict:
    return {"revisions": rows, "summary": {"actions": {}}}


# ---------------------------------------------------------------------------
# 1) revised_card
# ---------------------------------------------------------------------------
def test_revised_card():
    r = revision("RV1", "银行资产质量", "merge_fix", q="「银行资产质量」的定义是什么？",
                 a="银行资产质量：不良率下降。", source="《rp_B》 第2章 p3-5", theme="金融/地产")
    card_out = cra.revised_card(r)
    assert set(card_out) == set(cra.CARD_FIELDS)
    assert card_out["a"] == "银行资产质量：不良率下降。"
    assert card_out["q"] == "「银行资产质量」的定义是什么？"
    assert card_out["source"] == "《rp_B》 第2章 p3-5"
    assert card_out["topic"] == "金融/资本市场", "主题→topic 映射"
    assert card_out["evidence"] == "银行资产质量证据"


# ---------------------------------------------------------------------------
# 2) build_plan
# ---------------------------------------------------------------------------
def test_build_plan_add_replace_drop():
    existing = [card("银行资产质量"), card("黄金柱"), card("涨停密码", topic="量学/涨停"),
                card("涨停密码", topic="量学/涨停")]
    revs = revisions_payload([
        revision("RV1", "新增概念X", "merge_fix", q="Q1", a="A1"),
        revision("RV2", "银行资产质量", "merge_fix", q="Q2", a="A2改进"),
        revision("RV3", "黄金柱", "drop"),        # 概念已存在 → 保留已有卡不删
        revision("RV4", "未合并概念Y", "drop"),    # 未合并过 → no-op
    ])
    plan = cra.build_plan(revs, existing)
    s = plan["stats"]
    assert s["before"] == 4 and s["after"] == 5
    assert s["added"] == 1 and s["replaced"] == 1
    assert s["drop_merge_existing"] == 1 and s["drop_skip_absent"] == 1
    ops = {op["op"]: op for op in plan["ops"]}
    assert ops["add"]["concept"] == "新增概念X"
    assert ops["replace"]["index"] == 0 and ops["replace"]["after"]["a"] == "A2改进"
    # 现有卡顺序保留：index 0 被替换，其他原样
    assert plan["resulting_cards"][1]["concept"] == "黄金柱"
    assert len([c for c in plan["resulting_cards"] if c["concept"] == "涨停密码"]) == 2


# ---------------------------------------------------------------------------
# 3) validate_result
# ---------------------------------------------------------------------------
def test_validate_result():
    before = [card("A"), card("B")]
    assert cra.validate_result([card("A"), card("B"), card("C")], before) == []
    # 缺字段
    bad = [card("A"), card("B"), {"q": "x", "concept": "C"}]
    errs = cra.validate_result(bad, before)
    assert any("缺字段" in e for e in errs)
    # 删卡/丢 concept
    errs2 = cra.validate_result([card("A")], before)
    assert any("减少" in e or "丢失" in e for e in errs2)
    # 新增重复 concept
    errs3 = cra.validate_result([card("A"), card("B"), card("C"), card("C")], before)
    assert any("重复" in e for e in errs3)
    # 现有重复（涨停密码 x2）保持原样不报错
    dup_before = [card("涨停密码"), card("涨停密码")]
    assert cra.validate_result([card("涨停密码"), card("涨停密码"), card("新卡")], dup_before) == []


# ---------------------------------------------------------------------------
# 4) dry-run 不写盘
# ---------------------------------------------------------------------------
def test_dryrun_no_write(tmp_path):
    revs = revisions_payload([revision("RV1", "新概念", "merge_fix", q="Q", a="A")])
    cards_payload = {"cards": [card("旧卡")], "total_units": 100}
    revisions_path = tmp_path / "revs.json"
    cards_path = tmp_path / "cards.json"
    revisions_path.write_text(json.dumps(revs, ensure_ascii=False), encoding="utf-8")
    cards_path.write_text(json.dumps(cards_payload, ensure_ascii=False), encoding="utf-8")
    before = sha_text(cards_path)
    backup = tmp_path / "bak.json"
    report = cra.build(revisions_path=revisions_path, cards_path=cards_path,
                       backup_path=backup, out_dir=tmp_path, apply=False)
    assert report["mode"] == "dry-run" and report["apply"] is False
    assert report["plan"]["stats"]["before"] == 1 and report["plan"]["stats"]["after"] == 2
    assert sha_text(cards_path) == before, "dry-run 不得写学习卡"
    assert not backup.exists(), "dry-run 不得生成备份"


# ---------------------------------------------------------------------------
# 5) --apply（tmp）：备份 + 写盘 + 校验 + digest(mock) + 幂等
# ---------------------------------------------------------------------------
def test_apply_backup_validation_idempotent(tmp_path):
    revs = revisions_payload([
        revision("RV1", "新概念", "merge_fix", q="Q1", a="A1"),
        revision("RV2", "旧卡", "merge_fix", q="Q2", a="A2改进"),
        revision("RV3", "未合并概念", "drop"),
    ])
    cards_payload = {"cards": [card("旧卡")], "total_units": 100, "topics": {}}
    revisions_path = tmp_path / "revs.json"
    cards_path = tmp_path / "cards.json"
    revisions_path.write_text(json.dumps(revs, ensure_ascii=False), encoding="utf-8")
    cards_path.write_text(json.dumps(cards_payload, ensure_ascii=False), encoding="utf-8")
    backup = tmp_path / "kb_learning_cards.test_bak2.json"
    refresh_calls = []

    class FakeProc:
        returncode = 0
        stdout = "生成成功：cards=2"
        stderr = ""

    def fake_refresh(cmd, **kwargs):
        refresh_calls.append(list(cmd))
        return FakeProc()

    before_bytes = cards_path.read_bytes()
    report = cra.build(revisions_path=revisions_path, cards_path=cards_path,
                       backup_path=backup, out_dir=tmp_path, apply=True,
                       refresh_runner=fake_refresh)
    assert report["ok"] is True, report.get("errors") or report["validation"]
    assert backup.is_file() and backup.read_bytes() == before_bytes, "备份应为写前快照"
    written = json.loads(cards_path.read_text(encoding="utf-8"))
    assert len(written["cards"]) == 2
    assert written["revision_apply"]["added"] == 1 and written["revision_apply"]["replaced"] == 1
    assert report["validation"]["ok"] is True
    assert report["validation"]["count_before"] == 1 and report["validation"]["count_after"] == 2
    assert refresh_calls and report["digest"]["ok"] is True
    assert report["rollback"]["backup"].endswith("test_bak2.json")

    # 幂等：再次 apply（concepts 已在）→ 全部 replace，数量不变
    report2 = cra.build(revisions_path=revisions_path, cards_path=cards_path,
                        backup_path=backup, out_dir=tmp_path, apply=True,
                        refresh_runner=fake_refresh)
    written2 = json.loads(cards_path.read_text(encoding="utf-8"))
    assert len(written2["cards"]) == 2, "二次 apply 数量不变（幂等）"
    assert report2["plan"]["stats"]["added"] == 0 and report2["plan"]["stats"]["replaced"] == 2
