# -*- coding: utf-8 -*-
"""HQ 会议纪要草稿器测试（C2，task-39）。

覆盖：完整五角色草稿、缺角色不伪造、无证据 unverified、坏意见 JSON、非法意见、
JSON/Markdown 输出、--out 临时文件、生产写入拦截（--out 生产目录 / --write）、
议程缺失/失败、与 hq_meeting_prep 真实输出集成。
全部 dry-run；不写生产会议。
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
TOOL = ROOT / "tools" / "hq_meeting_record.py"
PREP = ROOT / "tools" / "hq_meeting_prep.py"
PROD_MEETINGS = ROOT / "agent_platform" / "data" / "meetings" / "hq"

spec = importlib.util.spec_from_file_location("hq_meeting_record_under_test", TOOL)
hmr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hmr)

ROLE_ORDER = ("TIANFENG", "ZHUGE", "ZHAOYUN", "HUANGZHONG", "GUANYU")
MID_RE = re.compile(r"^hq-\d{8}-\d{6}$")


def _run(args, cwd=ROOT):
    p = subprocess.run([PY, "-X", "utf8", str(TOOL), *args], cwd=str(cwd),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=180)
    return p.returncode, p.stdout or "", p.stderr or ""


def _agenda(tmp_path: Path, topic_id="HQ-20261103-01", ok=True) -> Path:
    topics = [{
        "order": 1, "topic_id": topic_id, "kind": "研发",
        "title": "门禁 flaky 根因与修复排期", "owner": "ARCH1", "legion": "hq-command",
        "due": "2026-11-03", "due_state": "due_soon",
        "roles_expected": list(ROLE_ORDER), "role_prompts": {},
        "evidence_needed": ["meeting:...", "五个 RoleOpinion"],
        "desired_output": "方案+工单+验收命令", "agenda_items": ["①背景", "②意见", "③决议", "④派单"],
        "priority": {"category": ["未派单"], "score": 100, "directive_id": "DIR-1",
                     "status": "open", "task_id": ""},
        "source_refs": [], "read_only": True,
    }]
    doc = {"schema": "hq/meeting_prep/v1", "ok": ok, "read_only": True, "no_write": True,
           "date": "2026-11-03", "generated_at": "2026-11-03T07:00:00+0800",
           "sources": {}, "selection": {"candidates": 1, "selected": 1, "max_topics": 3},
           "roles": [{"role": r, "name": hmp_name(r), "seat": hmp_seat(r),
                      "duty": "duty", "never": [], "model": "m", "prompt_exists": False}
                     for r in ROLE_ORDER],
           "topics": topics, "agenda_items": [{"order": 1, "topic_id": topic_id}],
           "warnings": []}
    if not ok:
        doc.update({"error": "no_source_data", "topics": []})
    p = tmp_path / "agenda.json"
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return p


def hmp_name(role):
    return hmr.ROLE_CN[role]


def hmp_seat(role):
    return {"TIANFENG": "A0", "ZHUGE": "AI1"}.get(role, role)


def _opinion(role, topic_id="HQ-20261103-01", position="support",
             verified=True, recommendation="建议按方案执行", **over) -> dict:
    evidence = [{"kind": "file", "ref": "deliverables/xx.md",
                 "verified": verified}] if verified is not None else []
    doc = {
        "schema": "hq_role_opinion/v1", "topic_id": topic_id,
        "role": role, "round": 1, "position": position,
        "recommendation": recommendation, "evidence": evidence,
        "risks": ["R1"], "conditions": ["C1"] if position == "conditional" else [],
        "escalate": False, "escalate_reason": "",
        "created_at": "2026-11-03T06:50:00+08:00",
    }
    doc.update(over)
    return doc


def _write_opinions(tmp_path: Path, opinions, name="opinions.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(opinions, ensure_ascii=False), encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# 1. 完整五角色 → 可生成纪要草稿（dry-run；不写生产）
# --------------------------------------------------------------------------- #
def test_complete_draft(tmp_path):
    before = sorted(p.name for p in PROD_MEETINGS.glob("hq-*.json"))
    agenda = _agenda(tmp_path)
    ops = _write_opinions(tmp_path, [_opinion(r) for r in ROLE_ORDER])
    rc, out, err = _run(["--agenda", str(agenda), "--opinions", str(ops), "--json"])
    assert rc == 0, err
    doc = json.loads(out)
    assert doc["ok"] is True and doc["dry_run"] is True
    m = doc["meeting"]
    assert m["schema_version"] == "2.0" and m["status"] == "draft"
    assert m["source_verification"] == "dry_run_draft_not_server_recorded"
    assert m["meeting_id"].endswith("-draft") and not MID_RE.fullmatch(m["meeting_id"])
    assert len(m["agenda"]) == 1
    assert len(m["transcript"]) == 5
    assert doc["summary"]["verified"] == 5 and doc["summary"]["missing"] == 0
    assert m["decisions"] == [] and m["orders"] == []
    assert "A0" in m["attendees"]
    after = sorted(p.name for p in PROD_MEETINGS.glob("hq-*.json"))
    assert before == after  # 未写生产会议


# --------------------------------------------------------------------------- #
# 2. 缺角色 → missing 显式，不伪造 transcript
# --------------------------------------------------------------------------- #
def test_missing_roles_not_fabricated(tmp_path):
    agenda = _agenda(tmp_path)
    ops = _write_opinions(tmp_path, [_opinion("TIANFENG"), _opinion("ZHUGE")])
    rc, out, _ = _run(["--agenda", str(agenda), "--opinions", str(ops), "--json"])
    assert rc == 0
    doc = json.loads(out)
    assert doc["summary"]["verified"] == 2
    assert doc["summary"]["missing"] == 3
    by = {r["role"]: r["status"] for r in doc["role_opinion_status"]}
    assert by["ZHAOYUN"] == "missing" and by["HUANGZHONG"] == "missing"
    transcript_roles = {t["role"] for t in doc["meeting"]["transcript"]}
    assert transcript_roles == {"TIANFENG", "ZHUGE"}
    assert "ZHAOYUN" not in transcript_roles


# --------------------------------------------------------------------------- #
# 3. 无证据 → unverified（不冒充 verified）
# --------------------------------------------------------------------------- #
def test_unverified_without_evidence(tmp_path):
    agenda = _agenda(tmp_path)
    ops = _write_opinions(tmp_path, [_opinion("GUANYU", verified=None)])
    rc, out, _ = _run(["--agenda", str(agenda), "--opinions", str(ops), "--json"])
    assert rc == 0
    doc = json.loads(out)
    assert doc["summary"]["unverified"] == 1 and doc["summary"]["verified"] == 0
    row = [r for r in doc["role_opinion_status"] if r["role"] == "GUANYU"][0]
    assert row["status"] == "unverified" and row["verified_evidence"] == 0
    tr = [t for t in doc["meeting"]["transcript"] if t["role"] == "GUANYU"][0]
    assert tr["verified"] is False


# --------------------------------------------------------------------------- #
# 4. 坏意见 JSON → fail-visible rc=2
# --------------------------------------------------------------------------- #
def test_bad_opinions_json_fail_visible(tmp_path):
    agenda = _agenda(tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    rc, out, err = _run(["--agenda", str(agenda), "--opinions", str(bad), "--json"])
    assert rc == 2
    doc = json.loads(out)
    assert doc["ok"] is False
    assert "opinions_bad_json" in doc["error"]
    assert "FAIL" in err


# --------------------------------------------------------------------------- #
# 5. 非法意见字段 → invalid 显式（不静默放行）
# --------------------------------------------------------------------------- #
def test_invalid_opinion_explicit(tmp_path):
    agenda = _agenda(tmp_path)
    ops = _write_opinions(tmp_path, [_opinion("HUANGZHONG", position="maybe")])
    rc, out, _ = _run(["--agenda", str(agenda), "--opinions", str(ops), "--json"])
    assert rc == 2  # 有 invalid → 不能当成功
    doc = json.loads(out)
    assert doc["ok"] is False
    assert doc["summary"]["invalid"] == 1
    row = [r for r in doc["role_opinion_status"] if r["role"] == "HUANGZHONG"][0]
    assert row["status"] == "invalid" and row["errors"]


# --------------------------------------------------------------------------- #
# 6. JSON / Markdown 输出（默认 = hq_meeting v2 JSON）
# --------------------------------------------------------------------------- #
def test_json_and_markdown_output(tmp_path):
    agenda = _agenda(tmp_path)
    rc, out, _ = _run(["--agenda", str(agenda)])
    assert rc == 0
    doc = json.loads(out)  # 默认输出 JSON 且可解析
    assert doc["meeting"]["schema_version"] == "2.0"
    rc2, out2, _ = _run(["--agenda", str(agenda), "--markdown"])
    assert rc2 == 0
    assert "# 五角色晨会纪要草稿" in out2 and "dry-run" in out2
    assert "五角色意见状态" in out2 and "decisions" in out2
    assert "不伪造" in out2


# --------------------------------------------------------------------------- #
# 7. --out 临时文件 + 生产目录拦截 + --write 未实现
# --------------------------------------------------------------------------- #
def test_out_and_production_guard(tmp_path):
    agenda = _agenda(tmp_path)
    out_json = tmp_path / "record.json"
    rc, out, _ = _run(["--agenda", str(agenda), "--out", str(out_json)])
    assert rc == 0 and out_json.is_file()
    assert "已写出" in out
    assert json.loads(out_json.read_text(encoding="utf-8"))["meeting"]["status"] == "draft"
    # 生产会议目录：拒绝
    evil = PROD_MEETINGS / "hq-20261103-999999.json"
    rc2, out2, err2 = _run(["--agenda", str(agenda), "--out", str(evil)])
    assert rc2 == 2 and not evil.exists()
    assert "production_write_forbidden" in out2 + err2
    # --write：本单不实现
    rc3, out3, err3 = _run(["--agenda", str(agenda), "--write"])
    assert rc3 == 2 and "production_write_disabled" in (out3 + err3)


# --------------------------------------------------------------------------- #
# 8. 议程缺失 / 坏 / 失败 → fail-visible
# --------------------------------------------------------------------------- #
def test_missing_or_bad_agenda(tmp_path):
    rc, out, err = _run(["--agenda", str(tmp_path / "nope.json"), "--json"])
    assert rc == 2 and "agenda_read_error" in out
    bad = tmp_path / "bad_agenda.json"
    bad.write_text("{oops", encoding="utf-8")
    rc2, out2, _ = _run(["--agenda", str(bad), "--json"])
    assert rc2 == 2 and "agenda_bad_json" in out2
    fail_agenda = _agenda(tmp_path, ok=False)
    rc3, out3, _ = _run(["--agenda", str(fail_agenda), "--json"])
    assert rc3 == 2 and "agenda_not_ok" in out3


# --------------------------------------------------------------------------- #
# 9. 与 hq_meeting_prep 真实输出集成（真实议程 → 草稿）
# --------------------------------------------------------------------------- #
def test_real_prep_integration(tmp_path):
    agenda_path = tmp_path / "real_agenda.json"
    p = subprocess.run([PY, "-X", "utf8", str(PREP), "--json", "--out", str(agenda_path)],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180)
    if p.returncode != 0 and not agenda_path.is_file():
        # 上游暂无真源数据时不强求成功；但这里应正常（真实仓库有 directives）
        raise AssertionError("hq_meeting_prep failed: %s %s" % (p.returncode, p.stderr[:300]))
    doc = json.loads(agenda_path.read_text(encoding="utf-8"))
    assert doc["schema"] == "hq/meeting_prep/v1" and doc["ok"] is True
    topic_id = doc["topics"][0]["topic_id"]
    ops = _write_opinions(tmp_path, [_opinion(r, topic_id=topic_id) for r in ROLE_ORDER])
    rc, out, err = _run(["--agenda", str(agenda_path), "--opinions", str(ops), "--json"])
    assert rc == 0, err
    draft = json.loads(out)
    assert draft["ok"] is True
    assert draft["meeting"]["agenda"][0]["topic_id"] == topic_id
    assert draft["summary"]["verified"] == len(ROLE_ORDER)
