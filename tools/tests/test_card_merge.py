# -*- coding: utf-8 -*-
"""学习卡合并工具（card_merge）回归测试。

覆盖：
  1) 仅 pass=true 合并（revise/reject/未审核跳过并分类统计）；
  2) 按 concept 去重：现有优先（冲突记录）+ 批内重复去重；
  3) 保留现有卡顺序 + 新卡按审核稿主题顺序追加 + 学习卡 schema/topic 映射；
  4) 写后校验允许现有重复 concept、拦截新增重复；
  5) 默认 dry-run 不写 base + 预览 json/md；
  6) --apply：写前备份 + 写后校验 + 原卡前缀保留；非法输入拒绝写盘。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "kb_corpus" / "card_merge.py"

_spec = importlib.util.spec_from_file_location("card_merge_tool", SCRIPT)
cm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cm)  # type: ignore[union-attr]


def make_base(cards: list[dict] | None = None) -> dict:
    cards = cards if cards is not None else [
        {"q": "什么是「黄金柱」？", "a": "黄金柱答案", "concept": "黄金柱", "source": "《伏击涨停》p74-78",
         "topic": "量学/涨停", "evidence": "黄金柱证据"},
        {"q": "什么是「煤电价值」？", "a": "煤电答案", "concept": "煤电价值", "source": "《rp_A》p1-4",
         "topic": "新能源/电力", "evidence": "煤电证据"},
    ]
    return {"generated_at": "2026-11-01T00:00:00+08:00", "total_units": 100,
            "cards": cards, "topics": {"量学/涨停": 1, "新能源/电力": 1},
            "daily": {"q": "旧daily"}, "file": "C:/x/cards.md"}


def proposal(pid: str, concept: str, theme: str = "AI算力/硬件", *, pass_: bool = False,
             revise: bool = False, reject: bool = False) -> dict:
    return {
        "id": pid, "status": "pending_review", "theme": theme,
        "q": f"什么是「{concept}」？", "a": f"{concept}的答案与数据支撑。",
        "concept": concept, "source": "《rp_X》 章节 p1-3", "topic": theme,
        "evidence": f"{concept}的原文证据", "reason": "缺口建议",
        "review": {"verdict": "", "checkboxes": {"pass": pass_, "revise": revise, "reject": reject},
                   "reviewer": "", "notes": ""},
    }


def make_proposals(cards: list[dict]) -> dict:
    return {"schema_version": 1, "date": "20261101", "cards": cards,
            "groups": [{"theme": c["theme"], "card_ids": [c["id"]]} for c in cards]}


def base_card(concept: str, topic: str = "AI/科技") -> dict:
    return {"q": f"什么是「{concept}」？", "a": f"{concept}答案", "concept": concept,
            "source": "《rp》p1-3", "topic": topic, "evidence": f"{concept}证据"}


# ---------------------------------------------------------------------------
# 1) 仅 pass=true
# ---------------------------------------------------------------------------
def test_plan_merge_only_passed():
    proposals = [proposal("P1", "新卡1", pass_=True),
                 proposal("P2", "新卡2", revise=True),
                 proposal("P3", "新卡3", reject=True),
                 proposal("P4", "新卡4")]  # 未审核
    plan = cm.plan_merge(make_base()["cards"], proposals)
    stats = plan["stats"]
    assert stats["approved"] == 1
    assert stats["would_add"] == 1
    assert stats["skipped_not_reviewed"] == 1
    assert stats["skipped_needs_revision"] == 1
    assert stats["skipped_rejected"] == 1
    assert [item["proposal_id"] for item in plan["inserted"]] == ["P1"]
    reasons = {s["proposal_id"]: s["reason"] for s in plan["skipped"]}
    assert reasons == {"P2": "needs_revision", "P3": "rejected", "P4": "not_reviewed"}
    assert cm.is_passed(proposals[0]) is True and cm.is_passed(proposals[3]) is False


# ---------------------------------------------------------------------------
# 2) 去重：现有优先 + 批内重复
# ---------------------------------------------------------------------------
def test_dedupe_existing_wins_and_batch_duplicates():
    base_cards = make_base()["cards"]
    proposals = [proposal("P1", "黄金柱", "量学/交易", pass_=True),        # 与现有卡重名
                 proposal("P2", "新卡A", pass_=True),
                 proposal("P3", "新卡A", pass_=True)]                     # 批内重复
    plan = cm.plan_merge(base_cards, proposals)
    stats = plan["stats"]
    assert stats["would_add"] == 1, "只有一张真正新增"
    assert stats["skipped_concept_exists"] == 1
    assert stats["skipped_duplicate_in_batch"] == 1
    assert [item["proposal_id"] for item in plan["inserted"]] == ["P2"]
    conflicts = {c["proposal_id"]: c for c in plan["conflicts"]}
    assert conflicts["P1"]["resolution"] == "existing_wins"
    assert conflicts["P3"]["resolution"] == "first_wins"


# ---------------------------------------------------------------------------
# 3) 顺序 / schema / topic 映射
# ---------------------------------------------------------------------------
def test_order_and_schema_mapping():
    base = make_base()
    proposals = [proposal("P1", "AI新卡", "AI算力/硬件", pass_=True),
                 proposal("P2", "交运新卡", "交运/物流", pass_=True)]
    plan = cm.plan_merge(base["cards"], proposals)
    merged = plan["merged_cards"]
    # 现有卡原顺序保留，新卡按审核稿顺序追加
    assert [c["concept"] for c in merged] == ["黄金柱", "煤电价值", "AI新卡", "交运新卡"]
    new_card = merged[2]
    assert set(cm.CARD_FIELDS) <= set(new_card), "新卡必须符合现有学习卡 schema"
    assert new_card["topic"] == "AI/科技", "AI算力/硬件 应映射到现有 topic 词表"
    assert merged[3]["topic"] == "交运/物流", "映射不到时沿用主题名"
    assert new_card["q"] and new_card["a"] and new_card["source"] and new_card["evidence"]

    payload = cm.build_merged_payload(base, merged, plan["inserted"])
    assert payload["total_units"] == 100 and payload["daily"] == {"q": "旧daily"}
    assert payload["topics"]["AI/科技"] == 1
    assert payload["merge_info"]["inserted"] == 2
    assert cm.validate_merged(payload, base["cards"], len(merged)) == []


# ---------------------------------------------------------------------------
# 4) 写后校验：允许现有重复，拦截新增重复
# ---------------------------------------------------------------------------
def test_validate_merged_duplicates():
    dup_base = [base_card("涨停密码"), base_card("涨停密码"), base_card("其他")]  # 现有已有重复
    merged = dup_base + [base_card("新卡")]
    payload = {"cards": merged, "topics": {}}
    assert cm.validate_merged(payload, dup_base, len(merged)) == [], "不应因现有重复而失败"

    merged_bad = dup_base + [base_card("新卡"), base_card("新卡")]  # 新增重复
    errors = cm.validate_merged({"cards": merged_bad}, dup_base, len(merged_bad))
    assert any("新增了重复 concept" in e for e in errors), errors

    # 现有卡被改动也要报错
    tampered = [dict(c) for c in merged]
    tampered[0]["q"] = "被篡改"
    assert any("现有卡顺序/内容被改动" in e for e in cm.validate_merged({"cards": tampered}, dup_base, len(tampered)))


# ---------------------------------------------------------------------------
# 5) dry-run 不写 base + 预览输出 + CLI
# ---------------------------------------------------------------------------
def test_dry_run_no_write(tmp_path):
    base_path = tmp_path / "kb_learning_cards.json"
    proposals_path = tmp_path / "proposals.json"
    base_payload = make_base()
    base_path.write_text(json.dumps(base_payload, ensure_ascii=False), encoding="utf-8")
    proposals_path.write_text(json.dumps(make_proposals([proposal("P1", "新卡A", pass_=True)]), ensure_ascii=False),
                              encoding="utf-8")
    before = base_path.read_text(encoding="utf-8")

    preview = cm.merge(proposals_path, base_path, out_dir=tmp_path / "reports", apply=False)
    assert preview["dry_run"] is True and preview["apply"] is False
    assert preview["stats"]["would_add"] == 1
    assert preview["stats"]["resulting_cards"] == 3
    assert preview["validation"]["ok"] is True, preview["validation"]["errors"]
    assert base_path.read_text(encoding="utf-8") == before, "dry-run 不得写 base"
    assert (tmp_path / "reports" / "card_merge_preview_20261101.json").is_file()
    assert (tmp_path / "reports" / "card_merge_preview_20261101.md").is_file()
    md = (tmp_path / "reports" / "card_merge_preview_20261101.md").read_text(encoding="utf-8")
    assert "DRY-RUN" in md and "预计新增" in md

    # CLI dry-run JSON
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPT), "merge",
         "--proposals", str(proposals_path), "--base", str(base_path),
         "--out-dir", str(tmp_path / "reports2"), "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stderr
    cli = json.loads(proc.stdout)
    assert cli["dry_run"] is True and cli["stats"]["would_add"] == 1


# ---------------------------------------------------------------------------
# 6) --apply：备份 + 写后校验 + 非法输入拒绝写盘
# ---------------------------------------------------------------------------
def test_apply_backup_and_validation(tmp_path):
    base_path = tmp_path / "kb_learning_cards.json"
    proposals_path = tmp_path / "proposals.json"
    base_payload = make_base()
    base_path.write_text(json.dumps(base_payload, ensure_ascii=False), encoding="utf-8")
    proposals_path.write_text(json.dumps(make_proposals([proposal("P1", "新卡A", pass_=True),
                                                        proposal("P2", "黄金柱", "量学/交易", pass_=True)]),
                                           ensure_ascii=False), encoding="utf-8")
    out_dir = tmp_path / "reports"
    preview = cm.merge(proposals_path, base_path, out_dir=out_dir, apply=True)
    assert preview["applied"] is True, preview.get("apply_error")
    assert preview["post_write_validation"]["ok"] is True
    backup = Path(preview["backup"])
    assert backup.is_file() and backup.parent == out_dir, "写前 .bak 应备份到 out_dir"
    assert json.loads(backup.read_text(encoding="utf-8")) == base_payload, "备份内容应为写前快照"

    written = json.loads(base_path.read_text(encoding="utf-8"))
    assert [c["concept"] for c in written["cards"]] == ["黄金柱", "煤电价值", "新卡A"], "现有顺序保留、新卡追加"
    assert written["merge_info"]["inserted"] == 1

    # 非法 proposals JSON → 拒绝写盘、base 不变
    bad_base = tmp_path / "base2.json"
    bad_base.write_text(json.dumps(base_payload, ensure_ascii=False), encoding="utf-8")
    bad_proposals = tmp_path / "bad.json"
    bad_proposals.write_text("{not-json", encoding="utf-8")
    bad_before = bad_base.read_text(encoding="utf-8")
    bad_preview = cm.merge(bad_proposals, bad_base, out_dir=tmp_path / "reports3", apply=True)
    assert bad_preview["applied"] is False
    assert bad_base.read_text(encoding="utf-8") == bad_before, "校验失败时不得写盘"
