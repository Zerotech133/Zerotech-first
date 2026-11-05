# -*- coding: utf-8 -*-
"""建议 decisions + dry-run 预览（wave19 BF）回归测试。

覆盖：
  1) 建议 decisions schema：60 条覆盖 checklist 全部 review_id、映射正确（预审 pass→pass / revise→revise、无 reject）、notes 非空；
  2) 分布与来源分布（对账 wave18 BC 预审：36/24/0，AE 12/8、AV 24/16）；
  3) dry-run decide 集成：不传 apply → mode=dry-run、decided 60、approved 36、预计新增 36、合并后校验通过；
  4) **dry-run 不写盘**：checklist / 学习卡 / AE / AV proposals SHA256 前后一致（含 CLI 路径）；
  5) 预览 JSON/MD schema：mode=dry-run、apply=false、"人工确认后执行"说明、safety hashes 全 true、decisions sha256 对应。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
R = REPO / "research" / "reports"
CHECKLIST = R / "card_review_checklist_20261101.json"
PRECHECK = R / "card_precheck_20261101.json"
DECISIONS = R / "card_review_decisions_suggested_20261101.json"
PREVIEW_JSON = R / "card_review_suggested_preview_20261101.json"
PREVIEW_MD = R / "card_review_suggested_preview_20261101.md"
EXISTING = REPO / "portal" / "static" / "kb_learning_cards.json"
AE = R / "learning_card_proposals_20261101.json"
AV = R / "card_proposals_v2_20261101.json"
SCRIPT = REPO / "tools" / "kb_corpus" / "card_review.py"

_spec = importlib.util.spec_from_file_location("card_review_for_suggested", SCRIPT)
cr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cr)  # type: ignore[union-attr]


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8-sig"))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def hash_snapshot() -> dict:
    return {"checklist": sha(CHECKLIST), "existing_cards": sha(EXISTING),
            "ae_proposals": sha(AE), "av_proposals": sha(AV)}


# ---------------------------------------------------------------------------
# 1) decisions schema + 映射
# ---------------------------------------------------------------------------
def test_decisions_schema_and_mapping():
    checklist = load(CHECKLIST)
    precheck = load(PRECHECK)
    decisions = load(DECISIONS)
    assert decisions["schema_version"] == 1
    assert decisions["date"] == "20261101"
    assert decisions["policy"]["apply"] is False
    assert decisions["policy"]["manual_confirmation_required"] is True
    dec = decisions["decisions"]
    assert len(dec) == 60
    ids = [e["review_id"] for e in checklist["entries"]]
    assert set(dec) == set(ids)
    # 映射：预审 pass→pass、revise→revise；不自动 reject
    pre_by_id = {e["review_id"]: e for e in precheck["entries"]}
    for rid in ids:
        expected = pre_by_id[rid]["suggested_verdict"]
        if expected == "reject":
            expected = "revise"
        assert dec[rid]["verdict"] == expected, f"{rid}: {dec[rid]['verdict']} != {expected}"
        assert dec[rid]["notes"].strip(), f"{rid} notes 为空"
        assert dec[rid]["verdict"] in ("pass", "revise", "reject")
    assert not any(v["verdict"] == "reject" for v in dec.values())
    s = decisions["summary"]
    assert s["total"] == 60 and s["pass"] == 36 and s["revise"] == 24 and s["reject"] == 0


# ---------------------------------------------------------------------------
# 2) 分布对账（预审 / 按来源）
# ---------------------------------------------------------------------------
def test_distribution_matches_precheck():
    checklist = load(CHECKLIST)
    precheck = load(PRECHECK)
    decisions = load(DECISIONS)["decisions"]
    source = {e["review_id"]: e["source"] for e in checklist["entries"]}
    precheck_summary = precheck["summary"]
    assert precheck_summary["pass"] == 36 and precheck_summary["revise"] == 24 and precheck_summary["reject"] == 0
    for src in ("AE", "AV"):
        sub = [v["verdict"] for rid, v in decisions.items() if source[rid] == src]
        expected = precheck_summary["by_source"][src]
        assert sub.count("pass") == expected["pass"], src
        assert sub.count("revise") == expected["revise"], src
        assert len(sub) == expected["total"], src
    # notes 应包含预审依据（置信/score）
    assert any("置信" in v["notes"] and "score" in v["notes"] for v in decisions.values())


# ---------------------------------------------------------------------------
# 3) dry-run decide 集成
# ---------------------------------------------------------------------------
def test_dryrun_decide_integration(tmp_path):
    # 当前卡池已扩到 400；用“不含 36 张 approved concept 的 20 张历史基线”复现当时的 dry-run 语义。
    decisions = load(DECISIONS)["decisions"]
    checklist = {e["review_id"]: e for e in load(CHECKLIST)["entries"]}
    approved = {checklist[rid]["concept"] for rid, v in decisions.items()
                if v["verdict"] == "pass"}
    payload = load(EXISTING)
    base = [c for c in payload["cards"] if c.get("concept") not in approved][:20]
    assert len(base) >= 20, "当前卡池不足以构造 20 张历史基线"
    base_payload = dict(payload)
    base_payload["cards"] = base
    base_path = tmp_path / "base20.json"
    base_path.write_text(json.dumps(base_payload, ensure_ascii=False), encoding="utf-8")

    report = cr.decide(decisions_path=DECISIONS, checklist_path=CHECKLIST, base_path=base_path,
                       apply=False, merge=False)
    assert report["ok"] is True, report.get("errors")
    assert report["mode"] == "dry-run" and report["apply"] is False
    stats = report["decision_stats"]
    assert stats["decided"] == 60
    assert stats["pass"] == 36 and stats["revise"] == 24 and stats["reject"] == 0 and stats["pending"] == 0
    mp = report["merge_preview"]
    assert mp["approved_cards"] == 36
    assert mp["plan_stats"]["would_add"] == 36
    assert mp["plan_stats"]["skipped_concept_exists"] == 0
    assert mp["plan_stats"]["resulting_cards"] == len(base) + 36
    assert mp["validation"]["ok"] is True


# ---------------------------------------------------------------------------
# 4) dry-run 不写盘（in-process + CLI）
# ---------------------------------------------------------------------------
def test_dryrun_does_not_write():
    before = hash_snapshot()
    report = cr.decide(decisions_path=DECISIONS, checklist_path=CHECKLIST, base_path=EXISTING,
                       apply=False, merge=False)
    assert report["ok"] is True
    after = hash_snapshot()
    assert after == before, "in-process dry-run 不得写任何真实文件"

    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPT), "decide", "--decisions", str(DECISIONS)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, cwd=str(REPO))
    assert proc.returncode == 0, proc.stderr
    assert "dry-run" in proc.stdout and "决策 60 条" in proc.stdout
    assert hash_snapshot() == before, "CLI dry-run 不得写任何真实文件"


# ---------------------------------------------------------------------------
# 5) 预览 JSON/MD schema 与安全自证
# ---------------------------------------------------------------------------
def test_preview_schema_and_md():
    preview = load(PREVIEW_JSON)
    assert preview["mode"] == "dry-run" and preview["apply"] is False
    assert "人工确认" in preview["note"]
    assert preview["decision_stats"]["decided"] == 60
    assert preview["merge_preview"]["approved_cards"] == 36
    assert preview["merge_preview"]["plan_stats"]["would_add"] == 36
    assert preview["merge_preview"]["validation"]["ok"] is True
    # decisions 文件 sha256 对应
    assert preview["decisions_file"]["sha256"] == sha(DECISIONS)
    assert preview["decisions_file"]["path"].endswith("card_review_decisions_suggested_20261101.json")
    # safety：全部 unchanged
    hashes = preview["safety"]["hashes"]
    assert hashes and all(v["unchanged"] for v in hashes.values())
    assert preview["safety"]["applied"] is False
    # next steps 含人工确认/apply/merge 说明
    steps = " ".join(preview["next_steps"])
    assert "人工复核" in steps and "--apply" in steps and "--merge" in steps
    assert "不构成投资建议" in preview["disclaimer"]

    md = PREVIEW_MD.read_text(encoding="utf-8")
    for marker in ("dry-run 预览", "未 apply", "人工确认后执行", "预计合并新增卡", "不构成投资建议"):
        assert marker in md, marker
