# -*- coding: utf-8 -*-
"""学习卡机器预审（card_precheck）回归测试。

覆盖：
  1) 可追溯规则（完整→无 trace flag；缺 unit_id→reject；软缺失→revise）；
  2) 答案质量规则（过短 reject / 截断 revise / 概念覆盖低 revise）；
  3) 重复度与主题均衡（批内同 concept 主卡保留、非主卡 revise；主题超额 revise）；
  4) 报告 schema/统计一致 + 不修改 checklist（hash 不变）+ MD 输出；
  5) 真实清单 60 条：分布合计 60、主题超额 5 个、清单 SHA256 不变。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "kb_corpus" / "card_precheck.py"

_spec = importlib.util.spec_from_file_location("card_precheck_tool", SCRIPT)
pc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pc)  # type: ignore[union-attr]


def entry(rid: str, concept: str, *, theme: str = "金融/地产", angle: str = "definition",
          answer: str | None = None, evidence: str | None = None, source: str = "AE",
          unit_id: str | None = None, page_start=1, page_end=3, chapter: str = "第1章",
          source_ref: str = "《rp_A》 第1章 p1-3") -> dict:
    return {
        "review_id": rid, "source": source, "source_card_id": f"C-{rid}",
        "status": "pending_review", "theme": theme, "angle": angle,
        "angle_cn": {"definition": "概念定义", "key_fact": "关键事实", "impact": "影响传导"}.get(angle, "—"),
        "q": f"什么是「{concept}」？",
        "a": answer if answer is not None else f"{concept}的结论与数据支撑，包含具体数据（同比增长 45%）。",
        "concept": concept,
        "source_ref": source_ref,
        "unit_id": unit_id if unit_id is not None else f"u_{rid}",
        "book": "rp_A", "chapter": chapter,
        "page_start": page_start, "page_end": page_end,
        "evidence": evidence if evidence is not None else f"{concept}的原文依据：报告期内指标同比增长 45%，证据充分。",
        "reason": "测试",
        "review": {"checkboxes": {"pass": False, "revise": False, "reject": False},
                   "decision": {"verdict": "", "notes": ""}},
    }


def eval_one(e: dict, existing: list[dict] | None = None) -> dict:
    idx = pc.build_batch_index([e])
    return pc.precheck_entry(e, existing or [], idx)


# ---------------------------------------------------------------------------
# 1) 可追溯规则
# ---------------------------------------------------------------------------
def test_traceability_rules():
    good = eval_one(entry("REV-1", "银行资产质量"))
    assert good["suggested_verdict"] == "pass", good["reasons"]
    assert "TRACE_MISSING:unit_id" not in good["flags"]

    missing_unit = eval_one(entry("REV-2", "银行资产质量", unit_id=""))
    assert missing_unit["suggested_verdict"] == "reject"
    assert any("unit_id" in r for r in missing_unit["reasons"])

    missing_page = eval_one(entry("REV-3", "银行资产质量", page_start=None))
    assert missing_page["suggested_verdict"] == "reject"

    soft = eval_one(entry("REV-4", "银行资产质量", chapter=""))
    assert soft["suggested_verdict"] == "revise"
    assert "TRACE_INCOMPLETE" in soft["flags"]
    assert pc.traceability(entry("REV-5", "X"))["ok"] is True
    assert pc.traceability(entry("REV-6", "X", page_start=None))["ok"] is False


# ---------------------------------------------------------------------------
# 2) 答案质量规则
# ---------------------------------------------------------------------------
def test_answer_quality_rules():
    short = eval_one(entry("REV-7", "银行资产质量", answer="太短。"))
    assert short["suggested_verdict"] == "reject"
    assert "ANSWER_TOO_SHORT" in short["flags"]

    truncated = eval_one(entry("REV-8", "银行资产质量",
                               answer="银行资产质量改善但仍需关注风险，数据待补充…"))
    assert truncated["suggested_verdict"] == "revise"
    assert "ANSWER_TRUNCATED" in truncated["flags"]

    # 答案与原文无关（grounding<10%）→ reject
    ungrounded = eval_one(entry("REV-9", "银行资产质量",
                                answer="与概念无关的另一段描述文本，只谈行业景气度与产品发布。",
                                evidence="银行不良率下降、拨备覆盖率上升的原文证据。"))
    assert ungrounded["suggested_verdict"] == "reject"
    assert "EVIDENCE_LOW_OVERLAP" in ungrounded["flags"]
    # 答案有原文依据但未对齐概念 → revise
    incoherent = eval_one(entry("REV-9b", "银行资产质量",
                                answer="报告期内不良率下降，拨备覆盖率上升，风险抵补能力增强。",
                                evidence="银行不良率下降、拨备覆盖率上升的原文证据。"))
    assert incoherent["suggested_verdict"] == "revise"
    assert "Q_ANSWER_MISMATCH" in incoherent["flags"]

    low_ground = eval_one(entry("REV-10", "银行资产质量",
                                answer="完全脱离原文的答案，包含许多原文没有的推断与结论描述。",
                                evidence="银行不良率下降的证据。"))
    assert low_ground["suggested_verdict"] == "reject"
    assert any("EVIDENCE_LOW_OVERLAP" in f for f in low_ground["flags"])


# ---------------------------------------------------------------------------
# 3) 重复度 + 主题均衡
# ---------------------------------------------------------------------------
def test_duplication_and_theme_quota():
    # 与现有学习卡同 concept → revise
    existing = [{"concept": "银行资产质量", "a": "旧卡答案", "q": "什么是「银行资产质量」？",
                 "source": "《x》p1-2", "topic": "金融/资本市场", "evidence": "旧证据"}]
    dup_existing = eval_one(entry("REV-11", "银行资产质量"), existing)
    assert dup_existing["suggested_verdict"] == "revise"
    assert "DUP_EXISTING_CONCEPT" in dup_existing["flags"]

    # 批内同 concept：主卡保留（pass），非主卡 revise
    units = [entry("REV-12", "机器人产业趋势", theme="机械/制造",
                   answer="机器人产业趋势的核心结论与数据，2026 年销量增长 30%，订单饱满数据充分。"),
             entry("REV-13", "机器人产业趋势", theme="机械/制造",
                   answer="机器人产业趋势的核心结论与数据，2026 年销量增长 30%，订单饱满数据充分。")]
    result = pc.precheck({"entries": units}, [])
    by_id = {r["review_id"]: r for r in result["results"]}
    assert by_id["REV-12"]["suggested_verdict"] == "pass"
    assert by_id["REV-13"]["suggested_verdict"] == "revise"
    assert "DUP_BATCH_CONCEPT" in by_id["REV-13"]["flags"]

    # 主题均衡：4 张同主题 → 排名 >3 的标记超额
    theme_units = [entry(f"REV-2{i}", f"主题概念{i}", theme="交运/物流",
                         answer=f"主题概念{i}的结论与数据，2026 年增长 {i}0%，证据充分可追溯。")
                   for i in range(1, 5)]
    res2 = pc.precheck({"entries": theme_units}, [])
    over = [r for r in res2["results"] if "THEME_OVER_QUOTA" in r["flags"]]
    assert len(over) == 1
    assert res2["summary"]["theme_over_quota"] == ["交运/物流"]


# ---------------------------------------------------------------------------
# 4) 报告 schema + 不修改 checklist + MD
# ---------------------------------------------------------------------------
def test_build_report_schema_and_unchanged(tmp_path):
    entries = [entry(f"REV-3{i}", f"概念{i}", theme="金融/地产") for i in range(1, 5)]
    checklist = {"entries": entries}
    checklist_path = tmp_path / "checklist.json"
    checklist_path.write_text(json.dumps(checklist, ensure_ascii=False), encoding="utf-8")
    before = checklist_path.read_text(encoding="utf-8")
    existing_path = tmp_path / "cards.json"
    existing_path.write_text(json.dumps({"cards": []}, ensure_ascii=False), encoding="utf-8")

    report = pc.build(checklist_path=checklist_path, existing_path=existing_path,
                      out_dir=tmp_path, write=True)
    assert report["checklist_unchanged"] is True
    assert checklist_path.read_text(encoding="utf-8") == before, "不得修改 checklist"
    s = report["summary"]
    assert s["total"] == 4
    assert s["pass"] + s["revise"] + s["reject"] == 4
    assert set(s["confidence"]) <= {"high", "medium", "low"}
    assert isinstance(s["by_source"], dict) and isinstance(s["by_theme"], list)
    for r in report["entries"]:
        assert r["suggested_verdict"] in ("pass", "revise", "reject")
        assert r["confidence"] in ("high", "medium", "low")
        assert r["reasons"] and isinstance(r["score"], int)
    md = (tmp_path / "card_precheck_20261101.md").read_text(encoding="utf-8")
    assert "机器预审建议" in md and "建议分布" in md and "仅辅助人工审核" in md


# ---------------------------------------------------------------------------
# 5) 真实清单：60 条分布 + 主题超额 + 不修改源文件
# ---------------------------------------------------------------------------
def test_real_checklist_precheck(tmp_path):
    assert pc.CHECKLIST.is_file()
    sha_before = hashlib.sha256(pc.CHECKLIST.read_bytes()).hexdigest()
    report = pc.build(out_dir=tmp_path, write=True)
    s = report["summary"]
    assert s["total"] == 60
    assert s["pass"] + s["revise"] + s["reject"] == 60
    assert s["by_source"]["AE"]["total"] == 20 and s["by_source"]["AV"]["total"] == 40
    assert len(s["by_source"]["AE"]) >= 4
    assert set(s["theme_over_quota"]) >= {"消费/零售", "宏观/政策", "机械/制造", "军工/航天", "交运/物流"}
    assert report["checklist_unchanged"] is True
    assert hashlib.sha256(pc.CHECKLIST.read_bytes()).hexdigest() == sha_before, "不得修改 checklist"
    assert (tmp_path / "card_precheck_20261101.json").is_file()
    assert (tmp_path / "card_precheck_20261101.md").is_file()
