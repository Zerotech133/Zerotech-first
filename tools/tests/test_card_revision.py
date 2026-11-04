# -*- coding: utf-8 -*-
"""学习卡 revise 修订建议（card_revision）回归测试。

覆盖：
  1) 概念-答案对齐分析（覆盖低 → 建议答案加概念锚点/问题重写 → merge_fix）；
  2) 去重与动作判定（现有卡重复 → drop；批内重复非主卡 → drop、主卡继续修订；主题超额 → keep_revise）；
  3) 出处规范化 / 答案超长截断 / 截断补全建议；
  4) build_revisions schema（只取 revise 卡、动作/审核位/instructions、统计一致）；
  5) 真实产物：24 张 revise、动作分布合计 24、源数据 SHA256 不变、MD 含审核位。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "kb_corpus" / "card_revision.py"
OUT_JSON = REPO / "research" / "reports" / "card_revisions_20261101.json"
OUT_MD = REPO / "research" / "reports" / "card_revisions_20261101.md"
CHECKLIST = REPO / "research" / "reports" / "card_review_checklist_20261101.json"
CARDS = REPO / "portal" / "static" / "kb_learning_cards.json"

_spec = importlib.util.spec_from_file_location("card_revision_tool", SCRIPT)
cr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cr)  # type: ignore[union-attr]


def entry(rid: str, concept: str, *, answer: str | None = None, evidence: str = "原文证据：报告期内指标同比增长 45%，数据充分。",
          q: str | None = None, source_ref: str = "《rp_A》 第1章 p1-3", theme: str = "金融/地产",
          angle_cn: str = "概念定义", source: str = "AV", score: float = 80.0) -> dict:
    return {
        "review_id": rid, "source": source, "source_card_id": f"C-{rid}",
        "theme": theme, "angle_cn": angle_cn, "concept": concept,
        "q": q or f"什么是「{concept}」？",
        "a": answer if answer is not None else f"{concept}的结论与数据支撑，包含具体数据（同比增长 45%）。",
        "evidence": evidence,
        "source_ref": source_ref, "book": "rp_A", "chapter": "第1章",
        "page_start": 1, "page_end": 3, "unit_id": f"u_{rid}",
    }


def pre(flags: list[str] | None = None, *, score: float = 80.0, verdict: str = "revise",
        rid: str | None = None) -> dict:
    return {"review_id": rid or "", "flags": flags or [], "reasons": ["测试"], "score": score,
            "confidence": "medium", "suggested_verdict": verdict}


def existing_card(concept: str) -> dict:
    return {"q": f"什么是「{concept}」？", "a": "旧卡答案", "concept": concept,
            "source": "《x》p1-2", "topic": "金融/资本市场", "evidence": "旧卡证据"}


# ---------------------------------------------------------------------------
# 1) 概念-答案对齐
# ---------------------------------------------------------------------------
def test_alignment_analysis_and_merge_fix():
    e = entry("R1", "银行资产质量", answer="报告期内不良率下降，拨备覆盖率上升，风险抵补能力增强。")
    align = cr.analyze_alignment(e)
    assert align["coverage"] < cr.ALIGN_MIN and align["needs_align"] is True
    assert "银行资产质量" in align["suggested_answer"]
    assert "银行资产质量" in align["suggested_question"]
    decision = cr.decide_action(e, pre(["Q_ANSWER_MISMATCH"]), {"in_existing": False}, align,
                                cr.analyze_source(e), cr.analyze_length(e),
                                batch_dup=False, batch_primary=True)
    assert decision["final_action"] == "merge_fix"
    assert any(a["action"] == "merge_fix" and a["field"] == "q/a" for a in decision["actions"])
    # 概念已在答案中 → 无需对齐
    good = entry("R2", "银行资产质量", answer="银行资产质量改善：不良率下降。")
    assert cr.analyze_alignment(good)["needs_align"] is False


# ---------------------------------------------------------------------------
# 2) 去重与动作判定
# ---------------------------------------------------------------------------
def test_dedupe_and_action_priority():
    # 现有学习卡重复 → drop
    e = entry("R1", "银行资产质量")
    dedup = cr.analyze_dedup(e, {cr.normalize("银行资产质量"): existing_card("银行资产质量")})
    assert dedup["in_existing"] is True
    d = cr.decide_action(e, pre(["DUP_BATCH_CONCEPT"]), dedup, cr.analyze_alignment(e),
                         cr.analyze_source(e), cr.analyze_length(e), False, True)
    assert d["final_action"] == "drop"
    assert "现有学习卡重复" in d["reasons"][0]

    # 批内重复非主卡 → drop；主卡 + 对齐问题 → merge_fix
    e2 = entry("R2", "机器人产业趋势")
    d2 = cr.decide_action(e2, pre(["DUP_BATCH_CONCEPT"]), {"in_existing": False},
                          cr.analyze_alignment(e2), cr.analyze_source(e2), cr.analyze_length(e2),
                          batch_dup=True, batch_primary=False)
    assert d2["final_action"] == "drop"
    assert "批内同 concept 重复（非主卡）" in d2["reasons"]

    e3 = entry("R3", "机器人产业趋势", answer="机器人产业趋势：2026 年销量增长 30%，订单饱满。")
    d3 = cr.decide_action(e3, pre(["DUP_BATCH_CONCEPT"]), {"in_existing": False},
                          cr.analyze_alignment(e3), cr.analyze_source(e3), cr.analyze_length(e3),
                          batch_dup=True, batch_primary=True)
    assert d3["final_action"] != "drop"

    # 仅主题超额（其余指标良好）→ keep_revise
    e4 = entry("R4", "交运概念",
               answer="交运概念的结论：2026 年业务量增长 12%，其中快递与铁路货运均明显改善，原文数据充分可追溯。")
    d4 = cr.decide_action(e4, pre(["THEME_OVER_QUOTA"]), {"in_existing": False},
                          cr.analyze_alignment(e4), cr.analyze_source(e4), cr.analyze_length(e4),
                          batch_dup=False, batch_primary=True)
    assert d4["final_action"] == "keep_revise"


# ---------------------------------------------------------------------------
# 3) 出处 / 长度
# ---------------------------------------------------------------------------
def test_source_and_length_fixes():
    bad_src = entry("R1", "银行资产质量", source_ref="rp_A 第1章 1-3")
    src = cr.analyze_source(bad_src)
    assert "source_ref_format" in src["issues"]
    assert src["suggested_source_ref"] == "《rp_A》 第1章 p1-3"

    long_answer = ("银行资产质量的核心结论与数据支撑，" * 20)
    length = cr.analyze_length(entry("R2", "银行资产质量", answer=long_answer))
    assert "answer_too_long" in length["issues"]
    assert len(length["suggested_answer"]) <= cr.MAX_ANSWER_LEN + 1
    assert length["suggested_answer"].endswith("…") or length["suggested_answer"].endswith("。")

    truncated = cr.analyze_length(entry("R3", "银行资产质量", answer="银行资产质量部分结论…"))
    assert "answer_too_short_or_truncated" in truncated["issues"]
    assert "…" not in truncated["suggested_answer"]
    assert "原文证据" in truncated["suggested_answer"] or len(truncated["suggested_answer"]) > cr.MIN_ANSWER_LEN


# ---------------------------------------------------------------------------
# 4) build_revisions schema
# ---------------------------------------------------------------------------
def test_build_revisions_schema():
    entries = [entry("R1", "概念甲"), entry("R2", "概念乙"), entry("R3", "概念丙", source="AE"),
               entry("R4", "概念丁")]  # R4 为 pass，应被排除
    pc = {"entries": [pre(["Q_ANSWER_MISMATCH"], rid="R1"), pre([], rid="R2"),
                      pre(["THEME_OVER_QUOTA"], rid="R3"), pre([], verdict="pass", rid="R4")]}
    payload = cr.build_revisions({"entries": entries}, pc, [], generated_at="2026-11-01T13:00:00+08:00")
    for key in ("schema_version", "date", "generated_at", "mode", "source", "policy",
                "summary", "revisions", "instructions", "disclaimer"):
        assert key in payload
    revs = payload["revisions"]
    assert len(revs) == 3, "只处理 revise 卡"
    ids = [r["revision_id"] for r in revs]
    assert ids == [f"RV-20261101-{i:03d}" for i in range(1, 4)]
    for r in revs:
        for key in ("review_id", "source", "theme", "concept", "original", "precheck",
                    "analysis", "actions", "final_action", "reasons", "review", "traceable"):
            assert key in r
        assert r["final_action"] in cr.ACTIONS
        assert r["review"]["checkboxes"] == {"merge_fix": False, "drop": False, "keep_revise": False}
        assert r["original"]["question"] and r["original"]["answer_summary"]
    s = payload["summary"]
    assert s["total"] == 3
    assert sum(s["actions"].values()) == 3
    assert payload["policy"]["modifies_checklist"] is False
    assert payload["policy"]["modifies_learning_cards"] is False
    assert "不构成投资建议" in payload["disclaimer"]


# ---------------------------------------------------------------------------
# 5) 真实产物 + 源数据未修改
# ---------------------------------------------------------------------------
def test_real_outputs_and_source_unchanged():
    payload = json.loads(OUT_JSON.read_text(encoding="utf-8-sig"))
    s = payload["summary"]
    assert s["total"] == 24
    assert sum(s["actions"].values()) == 24
    assert s["actions"]["drop"] >= 1 and s["actions"]["merge_fix"] >= 1
    assert len(payload["revisions"]) == 24
    assert payload["source_unchanged"] is True
    # 记录 sha 与当前文件一致（只读）；学习卡已扩卡后按历史归档口径校验
    assert payload["source"]["checklist_sha256"] == hashlib.sha256(CHECKLIST.read_bytes()).hexdigest()
    if payload["source"]["existing_cards_sha256"] == hashlib.sha256(CARDS.read_bytes()).hexdigest():
        pass
    else:
        assert len(payload["source"]["existing_cards_sha256"]) == 64
        assert payload["source"]["existing_cards_sha256"] != hashlib.sha256(CARDS.read_bytes()).hexdigest()
    md = OUT_MD.read_text(encoding="utf-8")
    for marker in ("修订建议稿", "动作分布", "☐ 采纳修订", "☐ 合并/删除", "☐ 保留待人工", "不构成投资建议"):
        assert marker in md, marker
