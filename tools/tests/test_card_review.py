# -*- coding: utf-8 -*-
"""学习卡审核工作流（card_review）回归测试。

覆盖：
  1) 统一清单汇总（AE/AV → 60 候选格式；合成数据下 5 条、review_id 连续、☐审核位、可追溯）；
  2) decisions 加载/校验（review_id 与源 card_id 两种键、未知 id/非法 verdict、子集告警）；
  3) decisions 应用（pass→approved_for_merge / revise→needs_revision / reject→rejected + reviewed_at）；
  4) dry-run decide 不写盘（清单与学习卡均不变）+ 合并预览统计（复用 card_merge）；
  5) --apply/--merge（tmp）：写清单、备份学习卡、写后校验、原卡前缀保留、无 approved 时不写 base；
  6) 真实清单校验（CLI check 60 条通过，decide CLI dry-run）。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "kb_corpus" / "card_review.py"

_spec = importlib.util.spec_from_file_location("card_review_tool", SCRIPT)
cr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cr)  # type: ignore[union-attr]


def ae_card(cid: str, concept: str, theme: str = "AI算力/硬件") -> dict:
    return {"id": cid, "status": "pending_review", "theme": theme,
            "q": f"什么是「{concept}」？", "a": f"{concept}的答案与数据支撑。",
            "concept": concept, "source": "《rp_A》 章节 p1-3", "unit_id": f"u_{cid}",
            "book": "rp_A", "chapter": "章节", "page_start": 1, "page_end": 3,
            "evidence": f"{concept}的原文证据段落。", "reason": "AE 建议", "unit_quality": 80.0}


def av_card(cid: str, concept: str, theme: str = "金融/地产", angle: str = "definition") -> dict:
    return {"card_id": cid, "status": "pending_review", "theme": theme,
            "angle": angle, "angle_cn": {"definition": "概念定义", "key_fact": "关键事实",
                                         "impact": "影响传导"}.get(angle, "概念定义"),
            "q": f"什么是「{concept}」？", "a": f"{concept}的定义。",
            "concept": concept, "unit_id": f"u_{cid}", "book": "rp_B", "chapter": "5.",
            "page_start": 2, "page_end": 6, "evidence": f"{concept}的证据。",
            "reason": "AV 模板", "quality_score": 75.0, "traceable": True}


def synthetic_sources() -> tuple[dict, dict]:
    ae = {"cards": [ae_card("PROP-1", "AI 算力需求"), ae_card("PROP-2", "光模块出货")]}
    av = {"cards": [av_card("CP2-1", "银行资产质量", "金融/地产", "definition"),
                    av_card("CP2-2", "机器人产业", "机械/制造", "key_fact"),
                    av_card("CP2-3", "机器人趋势", "机械/制造", "impact")]}
    return ae, av


@pytest.fixture()
def checklist() -> dict:
    ae, av = synthetic_sources()
    return cr.build_checklist(ae, av, generated_at="2026-11-01T11:00:00+08:00",
                              ae_path="ae.json", av_path="av.json")


def base_cards() -> dict:
    return {"generated_at": "2026-11-01T00:00:00+08:00", "total_units": 100,
            "cards": [{"q": "什么是「旧卡」？", "a": "旧卡答案", "concept": "旧卡",
                       "source": "《伏击涨停》p1-2", "topic": "量学/涨停", "evidence": "旧卡证据"}],
            "topics": {"量学/涨停": 1}, "daily": None, "file": "x.md"}


# ---------------------------------------------------------------------------
# 1) 清单汇总
# ---------------------------------------------------------------------------
def test_build_checklist_checklist(checklist):
    assert cr.validate_checklist(checklist, expected_total=5) == []
    entries = checklist["entries"]
    assert len(entries) == 5
    assert [e["review_id"] for e in entries] == [f"REV-20261101-{i:03d}" for i in range(1, 6)]
    assert [e["source"] for e in entries] == ["AE", "AE", "AV", "AV", "AV"]
    assert all(e["status"] == "pending_review" for e in entries)
    assert all(set(e["review"]["checkboxes"]) == {"pass", "revise", "reject"} for e in entries)
    assert all(set(e["review"]["checkboxes"].values()) == {False} for e in entries)
    assert all(e["unit_id"] and e["evidence"] and isinstance(e["page_start"], int) for e in entries)
    assert checklist["summary"]["by_source"] == {"AE": 2, "AV": 3}
    assert checklist["summary"]["total"] == 5
    assert checklist["summary"]["traceable"] == 5
    # AV 出处自动拼接
    assert checklist["entries"][2]["source_ref"].startswith("《rp_B》")
    md = cr.render_checklist_md(checklist)
    for mark in ("☐通过", "☐修改", "☐驳回", "decisions JSON"):
        assert mark.replace(" ", "") in md.replace(" ", "")


# ---------------------------------------------------------------------------
# 2) decisions 校验
# ---------------------------------------------------------------------------
def test_decisions_load_validation(checklist):
    entries = checklist["entries"]
    payload = {"reviewer": "tester", "decisions": {
        "REV-20261101-001": "pass",
        "CP2-2": {"verdict": "revise", "notes": "补来源"},   # 源 card_id 键
        "PROP-2": "reject",
    }}
    decisions, errors, warnings = cr.load_decisions(payload, entries)
    assert errors == []
    assert set(decisions) == {"REV-20261101-001", "REV-20261101-004", "REV-20261101-002"}
    assert decisions["REV-20261101-001"]["reviewer"] == "tester"
    assert decisions["REV-20261101-004"]["notes"] == "补来源"
    assert warnings and "部分未决策" in warnings[0]

    bad, errors2, _ = cr.load_decisions({"decisions": {"REV-999": "pass", "REV-20261101-001": "maybe"}}, entries)
    assert bad == {}
    assert any("未知" in e for e in errors2)
    assert any("verdict" in e for e in errors2)


# ---------------------------------------------------------------------------
# 3) decisions 应用（状态映射）
# ---------------------------------------------------------------------------
def test_apply_decisions_status_mapping(checklist):
    decisions = {
        "REV-20261101-001": {"verdict": "pass", "notes": "", "reviewer": "r"},
        "REV-20261101-002": {"verdict": "revise", "notes": "改", "reviewer": "r"},
        "REV-20261101-003": {"verdict": "reject", "notes": "驳", "reviewer": "r"},
    }
    updated = cr.apply_decisions(checklist, decisions, decided_at="2026-11-01T12:00:00+08:00")
    by_id = {e["review_id"]: e for e in updated["entries"]}
    assert by_id["REV-20261101-001"]["status"] == "approved_for_merge"
    assert by_id["REV-20261101-001"]["review"]["checkboxes"] == {"pass": True, "revise": False, "reject": False}
    assert by_id["REV-20261101-002"]["status"] == "needs_revision"
    assert by_id["REV-20261101-003"]["status"] == "rejected"
    assert by_id["REV-20261101-004"]["status"] == "pending_review"
    assert by_id["REV-20261101-001"]["review"]["decision"]["reviewed_at"] == "2026-11-01T12:00:00+08:00"
    stats = updated["summary"]["decision_stats"]
    assert stats["pass"] == 1 and stats["revise"] == 1 and stats["reject"] == 1 and stats["pending"] == 2
    # 原清单不被就地修改
    assert checklist["entries"][0]["status"] == "pending_review"


# ---------------------------------------------------------------------------
# 4) dry-run decide：不写盘 + 合并预览
# ---------------------------------------------------------------------------
def test_decide_dryrun_no_write(tmp_path, checklist):
    base = base_cards()
    base_path = tmp_path / "kb_learning_cards.json"
    base_path.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    before = base_path.read_text(encoding="utf-8")
    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(json.dumps({"reviewer": "t", "decisions": {
        "REV-20261101-001": "pass", "REV-20261101-004": "pass"}}, ensure_ascii=False), encoding="utf-8")

    report = cr.decide(decisions_path=decisions_path, base_path=base_path,
                       out_dir=tmp_path / "out", apply=False, checklist_payload=checklist)
    assert report["ok"] is True, report["errors"]
    assert report["mode"] == "dry-run"
    stats = report["decision_stats"]
    assert stats["pass"] == 2 and stats["pending"] == 3
    preview = report["merge_preview"]
    assert preview["approved_cards"] == 2
    assert preview["plan_stats"]["would_add"] == 2
    assert preview["validation"]["ok"] is True
    # dry-run 不写盘
    assert base_path.read_text(encoding="utf-8") == before
    assert not (tmp_path / "out").exists()
    md = cr.render_decide_md(report)
    assert "DRY-RUN" in md and "预计新增" in md


# ---------------------------------------------------------------------------
# 5) --apply / --merge（tmp）：写清单 + 备份 + 写后校验
# ---------------------------------------------------------------------------
def test_decide_apply_and_merge_tmp(tmp_path, checklist):
    base = base_cards()
    base_path = tmp_path / "kb_learning_cards.json"
    base_path.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(json.dumps({"reviewer": "t", "decisions": {
        "REV-20261101-001": "pass", "REV-20261101-002": "reject"}}, ensure_ascii=False), encoding="utf-8")
    out_dir = tmp_path / "out"
    report = cr.decide(decisions_path=decisions_path, base_path=base_path, out_dir=out_dir,
                       apply=True, merge=True, checklist_payload=checklist)
    assert report["ok"] is True, report.get("errors")
    # 清单已写
    jp = out_dir / "card_review_checklist_20261101.json"
    assert jp.is_file()
    saved = json.loads(jp.read_text(encoding="utf-8"))
    assert saved["entries"][0]["status"] == "approved_for_merge"
    assert saved["entries"][1]["status"] == "rejected"
    # 学习卡合并 + 备份 + 写后校验
    assert report["backup"] and Path(report["backup"]).is_file()
    written = json.loads(base_path.read_text(encoding="utf-8"))
    assert [c["concept"] for c in written["cards"]][0] == "旧卡", "现有卡顺序保留"
    assert any(c["concept"] == "AI 算力需求" for c in written["cards"]), "approved 卡应合并"
    assert report["post_write_validation"]["ok"] is True

    # 无 approved 卡时不应写 base
    decisions2 = tmp_path / "d2.json"
    decisions2.write_text(json.dumps({"decisions": {"REV-20261101-002": "reject"}}, ensure_ascii=False),
                          encoding="utf-8")
    # 重新读一次清单（已含 approved 1 张）
    report2 = cr.decide(decisions_path=decisions2, base_path=base_path, out_dir=tmp_path / "out2",
                        apply=True, merge=True, checklist_payload=saved)
    assert report2["ok"] is True
    assert report2["merged_out"] is None and any("无 approved" in w for w in report2["warnings"])


# ---------------------------------------------------------------------------
# 6) 真实清单：CLI check 60 条 + decide CLI dry-run
# ---------------------------------------------------------------------------
def test_real_checklist_and_decide_cli(tmp_path):
    real_checklist = REPO / "research" / "reports" / "card_review_checklist_20261101.json"
    payload, err = cr.read_json_tolerant(real_checklist)
    assert err is None
    assert len(payload["entries"]) == 60
    assert cr.validate_checklist(payload) == []
    assert payload["summary"]["by_source"] == {"AE": 20, "AV": 40}
    assert payload["summary"]["traceable"] == 60

    import subprocess
    import sys
    decisions = tmp_path / "d.json"
    decisions.write_text(json.dumps({"reviewer": "t", "decisions": {
        "REV-20261101-001": "pass", "CP2-20261101-001": "reject"}}, ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPT), "decide", "--decisions", str(decisions)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, cwd=str(REPO))
    assert proc.returncode == 0, proc.stderr
    assert "dry-run" in proc.stdout and "决策 2 条" in proc.stdout
    # 真实学习卡未被改动（dry-run）
    h1 = hashlib.sha256((REPO / "portal" / "static" / "kb_learning_cards.json").read_bytes()).hexdigest()
    proc2 = subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPT), "check"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, cwd=str(REPO))
    assert proc2.returncode == 0
    h2 = hashlib.sha256((REPO / "portal" / "static" / "kb_learning_cards.json").read_bytes()).hexdigest()
    assert h1 == h2
