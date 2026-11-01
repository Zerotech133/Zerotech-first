# -*- coding: utf-8 -*-
"""100 卡建议 decisions 二轮 dry-run 测试（task-97）。

覆盖：
  1) decisions schema/映射：100 张覆盖、precheck→verdict 映射、汇总 53/45/2、notes 含置信/理由；
  2) 与 card_review.py decide 兼容：临时合并清单（60+40）解析 100/100；真实清单可解析首轮 60（二轮 CP3 需合并清单，属已知边界）；
  3) dry-run 结果：ok、approved 53、base 88、预计新增 17、跳过 concept 已存在 36、合并后 105、校验通过；
  4) 不写盘安全：清单/学习卡/审核数据/扩卡候选 SHA256 前后一致；
  5) 预览 JSON/MD schema 与当前数据一致。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
S = REPO / "portal" / "static"
R = REPO / "research" / "reports"
REVIEW_DATA = S / "card_review_data.json"
CHECKLIST = R / "card_review_checklist_20261101.json"
CARDS = S / "kb_learning_cards.json"
PROPOSALS = S / "card_proposals_v3.json"
DECISIONS = R / "card_review_decisions_suggested_round2_20261101.json"
PREVIEW_JSON = R / "card_review_decisions_round2_preview_20261101.json"
PREVIEW_MD = R / "card_review_decisions_round2_preview_20261101.md"
SCRIPT = REPO / "tools" / "kb_corpus" / "card_review.py"

_spec = importlib.util.spec_from_file_location("cr_round2_test", SCRIPT)
cr = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = cr
_spec.loader.exec_module(cr)  # type: ignore[union-attr]


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8-sig"))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def combined_checklist() -> dict:
    cl = load(CHECKLIST)
    proposals = {c["card_id"]: c for c in load(PROPOSALS)["candidates"]}
    data = load(REVIEW_DATA)
    entries = [dict(e) for e in cl["entries"]]
    for it in data["items"]:
        if it.get("round") != "second":
            continue
        c = proposals[it["review_id"]]
        entries.append({
            "review_id": c["card_id"], "source": "AV", "source_card_id": c["card_id"],
            "theme": c.get("theme"), "angle": c.get("angle"), "angle_cn": c.get("angle_cn"),
            "q": c.get("q"), "a": c.get("a"), "concept": c.get("concept"),
            "evidence": c.get("evidence"), "book": c.get("book"), "chapter": c.get("chapter"),
            "page_start": c.get("page_start"), "page_end": c.get("page_end"),
            "unit_id": c.get("unit_id") or c["card_id"],
            "source_ref": f"《{c.get('book')}》 {c.get('chapter')} p{c.get('page_start')}-{c.get('page_end')}",
            "status": "pending_review", "traceable": c.get("traceable", True),
            "review": {"checkboxes": {"pass": False, "revise": False, "reject": False},
                       "decision": {"verdict": "", "notes": ""}},
        })
    return {
        "schema_version": 1, "date": "20261101",
        "generated_at": "2026-11-01T22:00:00+08:00",
        "sources": {"checklist": str(CHECKLIST), "proposals_v3": str(PROPOSALS)},
        "policy": {"purpose": "test dry-run only"},
        "summary": {"total": len(entries)},
        "entries": entries,
        "disclaimer": "临时合并清单仅供测试 dry-run；不构成投资建议。",
    }


# ---------------------------------------------------------------------------
# 1) decisions schema / 映射
# ---------------------------------------------------------------------------
def test_decisions_schema_and_mapping():
    d = load(DECISIONS)
    data = load(REVIEW_DATA)
    for key in ("schema_version", "date", "generator", "reviewer", "source", "policy",
                "decisions", "summary", "note", "disclaimer"):
        assert key in d, key
    dec = d["decisions"]
    assert len(dec) == 100
    ids = {it["review_id"] for it in data["items"]}
    assert set(dec) == ids
    mapping = {"pass": "pass", "revise": "revise", "reject": "reject"}
    for it in data["items"]:
        pre = it["precheck"]["suggested_verdict"]
        assert dec[it["review_id"]]["verdict"] == mapping[pre], it["review_id"]
        note = dec[it["review_id"]]["notes"]
        assert "预审建议" in note and "置信" in note and ("通过" in note or "需修改" in note or "驳回" in note)
    s = d["summary"]
    assert (s["total"], s["pass"], s["revise"], s["reject"]) == (100, 53, 45, 2)
    assert d["policy"]["apply"] is False and d["policy"]["manual_confirmation_required"] is True
    assert d["source"]["sha256"] == sha(REVIEW_DATA)


# ---------------------------------------------------------------------------
# 2) 与 decide 兼容：合并清单 100/100；真实清单首轮 60 可解析
# ---------------------------------------------------------------------------
def test_decide_compatibility():
    combined = combined_checklist()
    tmp = Path(tempfile.mkdtemp(prefix="cr97_test_")) / "combined.json"
    tmp.write_text(json.dumps(combined, ensure_ascii=False), encoding="utf-8")
    report = cr.decide(decisions_path=DECISIONS, checklist_path=tmp, base_path=CARDS,
                       apply=False, merge=False)
    assert report["ok"] is True, report.get("errors")
    assert report["decisions_count"] == 100
    assert report["mode"] == "dry-run" and report["apply"] is False
    ds = report["decision_stats"]
    assert ds["decided"] == 100 and ds["pass"] == 53 and ds["revise"] == 45 and ds["reject"] == 2
    # 真实清单（60 条）可解析首轮 60；二轮 CP3 需合并清单（已知边界，明确报未知 id）
    real = load(CHECKLIST)
    decisions, errors, _ = cr.load_decisions(load(DECISIONS), real["entries"])
    assert len(decisions) == 60
    assert len(errors) == 40 and all("CP3-" in e for e in errors)


# ---------------------------------------------------------------------------
# 3) dry-run 结果
# ---------------------------------------------------------------------------
def test_dryrun_result():
    p = load(PREVIEW_JSON)
    assert p["mode"] == "dry-run" and p["apply"] is False
    dr = p["dry_run"]
    assert dr["ok"] is True and dr["errors"] == []
    assert dr["parse"]["parse_100_100"] is True
    assert dr["parse"]["decoded"] == 100 and dr["parse"]["total"] == 100
    ds = dr["decision_stats"]
    assert ds["decided"] == 100 and ds["pass"] == 53 and ds["revise"] == 45 and ds["reject"] == 2
    mp = dr["merge_preview"]
    assert mp["approved_cards"] == 53
    assert mp["base_cards"] == 88
    plan = mp["plan_stats"]
    assert plan["would_add"] == 17, "二轮 pass17 为新增（首轮36 已在卡池中）"
    assert plan["skipped_concept_exists"] == 36
    assert plan["resulting_cards"] == 105
    assert plan["skipped_duplicate_in_batch"] == 0
    assert mp["validation"]["ok"] is True
    # conflicts = 与现有卡概念重复的 approved 卡（existing_wins 跳过），数量应与 skipped_concept_exists 一致
    conflicts = mp["conflicts"]
    assert len(conflicts) == plan["skipped_concept_exists"] == 36
    assert all(c.get("resolution") == "existing_wins" for c in conflicts)
    # 轮次对账
    assert p["by_round"]["first"] == {"total": 60, "pass": 36, "revise": 24, "reject": 0}
    assert p["by_round"]["second"] == {"total": 40, "pass": 17, "revise": 21, "reject": 2}


# ---------------------------------------------------------------------------
# 4) 不写盘安全 + 预览引用一致性
# ---------------------------------------------------------------------------
def test_no_writes_safety():
    p = load(PREVIEW_JSON)
    hashes = p["safety"]["hashes"]
    assert hashes and all(v["unchanged"] for v in hashes.values())
    assert p["safety"]["applied"] is False
    files = {"checklist": CHECKLIST, "learning_cards": CARDS,
             "card_review_data": REVIEW_DATA, "proposals_v3": PROPOSALS}
    for name, path in files.items():
        assert hashes[name]["before"] == sha(path) == hashes[name]["after"], name
    assert p["decisions_file"]["sha256"] == sha(DECISIONS)
    assert p["source"]["sha256"] == sha(REVIEW_DATA)


# ---------------------------------------------------------------------------
# 5) 预览 MD
# ---------------------------------------------------------------------------
def test_preview_markdown():
    md = PREVIEW_MD.read_text(encoding="utf-8")
    for marker in ("100 卡建议 decisions", "dry-run 预览", "建议 decisions 分布",
                   "decide dry-run 结果", "预计合并新增", "安全说明", "下一步",
                   "不构成投资建议"):
        assert marker in md, marker
    assert "100/100" in md or "100" in md
