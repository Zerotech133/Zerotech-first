# -*- coding: utf-8 -*-
"""HQ Directive 只读模型测试（C3，task-33）。

覆盖：真实数据冒烟、合成会议/议题字段映射、意见不伪造、显式任务链接、无数据 fail-visible、
坏文件跳过、JSON 纯度与过滤。全部只读；临时目录合成数据。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
TOOL = ROOT / "tools" / "hq_directives.py"

spec = importlib.util.spec_from_file_location("hq_directives_under_test", TOOL)
hd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hd)


def _run(args, cwd=ROOT):
    p = subprocess.run([PY, "-X", "utf8", str(TOOL), *args], cwd=str(cwd),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120)
    return p.returncode, p.stdout or "", p.stderr or ""


def _write_meeting(dirpath: Path, doc: dict) -> Path:
    dirpath.mkdir(parents=True, exist_ok=True)
    p = dirpath / ((doc.get("meeting_id") or "hq-20261103-060000") + ".json")
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return p


def _write_council(dirpath: Path, topics: list) -> None:
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / "council_new_topics.json").write_text(
        json.dumps(topics, ensure_ascii=False), encoding="utf-8")


def _write_tasks(path: Path, tasks: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tasks, ensure_ascii=False), encoding="utf-8")


def _meeting(decisions=None, orders=None, transcript=None, status="dispatched",
             mid="hq-20261103-060000") -> dict:
    return {
        "schema_version": "2.0",
        "meeting_id": mid,
        "created_at": "2026-11-03T06:00:00",
        "type": "morning",
        "agenda": [{"item": "议题", "owner": "A0", "minutes": 3}],
        "attendees": ["A0", "AI1", "ZHAOYUN", "HUANGZHONG", "GUANYU"],
        "transcript": transcript or [],
        "decisions": decisions or [],
        "orders": orders or [],
        "status": status,
    }


# ---------- 真实数据 ----------
def test_real_sources_directives_smoke():
    payload = hd.build_directives()
    assert payload["ok"] is True, payload.get("error")
    assert payload["read_only"] is True
    assert payload["sources"]["meetings"] >= 1
    assert payload["summary"]["total"] >= 1
    for d in payload["directives"]:
        for key in ("id", "topic", "source_role", "legion", "owner", "task_id",
                    "status", "due", "evidence", "opinions"):
            assert key in d, (d.get("id"), key)
        assert d["kind"] in hd.KINDS
        assert isinstance(d["evidence"], list) and d["evidence"]
        assert d["read_only"] is True
    # 真实 opinions 必须逐字来自真实会议 transcript（不伪造）
    real_texts = set()
    for p in sorted((ROOT / "agent_platform" / "data" / "meetings" / "hq").glob("hq-*.json")):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for m in doc.get("transcript") or []:
            if isinstance(m, dict) and m.get("text"):
                real_texts.add(str(m["text"]).strip()[: hd.MAX_TEXT])
    for d in payload["directives"]:
        for op in d["opinions"]:
            assert op["text"] in real_texts, "意见非真实 transcript：%r" % op["text"][:80]
            assert op["role"] in hd.ROLE_ORDER


# ---------- 合成会议映射 ----------
def test_synthetic_meeting_fields_and_opinions(tmp_path):
    meetings = tmp_path / "meetings"
    council = tmp_path / "council"
    tasks = tmp_path / "tasks.json"
    doc = _meeting(
        decisions=[{"id": "D1", "decision": "ACAD1 完成池契约 v1", "owner": "ACAD1",
                    "due": "2026-11-10", "evidence": ["baseline:1"], "dissent": []}],
        orders=[{"title": "补 9 项 flaky 根因", "type": "work", "owner": "GUANYU",
                 "state": "已生成任务申请",
                 "dispatch": {"ok": True, "data": {"id": "lg-abc123"}}}],
        transcript=[
            {"by": "A0", "text": "决议照此执行", "src": "live", "evidence": ["m:1"]},
            {"by": "ZHAOYUN", "text": "（模型调用失败：ConnectionResetError）",
             "src": "script", "evidence": []},
            {"by": "HOST", "text": "主持词不算角色意见", "src": "host", "evidence": []},
        ])
    _write_meeting(meetings, doc)
    _write_council(council, [])
    _write_tasks(tasks, {"T-1": {"status": "doing", "owner": "ACAD1", "desc": "x"}})
    payload = hd.build_directives(meetings, council, tasks)
    assert payload["ok"] is True
    by_id = {d["id"]: d for d in payload["directives"]}
    dec = by_id["DIR-MEET-hq-20261103-060000-D1"]
    assert dec["kind"] == "decision"
    assert dec["topic"].startswith("ACAD1 完成池契约")
    assert dec["owner"] == "ACAD1"
    assert dec["legion"] == "acad1-legion"
    assert dec["status"] == "open"
    assert dec["due"] == "2026-11-10"
    assert any("meeting:" in e for e in dec["evidence"])
    assert len(dec["opinions"]) == 2  # 仅 A0/ZHAOYUN 两席；HOST 不计
    dirs = {op["role"]: op for op in dec["opinions"]}
    assert dirs["TIANFENG"]["text"] == "决议照此执行"
    assert dirs["ZHAOYUN"]["model_call_failed"] is True
    assert "ZHUGE" in dec["roles_missing"]
    assert any(r["role"] == "TIANFENG" and r["duty"] for r in dec["roles_involved"])

    order = by_id["DIR-MEET-hq-20261103-060000-O1"]
    assert order["kind"] == "order"
    assert order["status"] == "dispatched"
    assert any("dispatch:inbox:lg-abc123" in e for e in order["evidence"])
    assert order["owner"] == "GUANYU"


def test_council_topic_directives(tmp_path):
    meetings = tmp_path / "meetings"
    council = tmp_path / "council"
    _write_meeting(meetings, _meeting())
    _write_council(council, [
        {"topic": "研发：HQ 主链闭环", "originator": "laotian",
         "created_at": "2026-11-02T10:00:00+08:00", "tags": ["研发"], "stage": "立题"},
        {"topic": "商业：订阅定价", "originator": "laotian",
         "created_at": "2026-11-02T11:00:00+08:00"},
    ])
    payload = hd.build_directives(meetings, council, tmp_path / "tasks.json")
    topics = [d for d in payload["directives"] if d["kind"] == "topic"]
    assert len(topics) == 2
    for t in topics:
        assert t["status"] == "open"
        assert t["opinions"] == []
        assert t["opinions_scope"] == "none"
        assert t["roles_missing"] == list(hd.ROLE_ORDER)
        assert t["evidence"][0].startswith("council:")
    assert all(d["id"].startswith("DIR-COUNCIL-") for d in topics)


def test_no_fabricated_opinions_when_no_role_seats(tmp_path):
    meetings = tmp_path / "meetings"
    council = tmp_path / "council"
    _write_meeting(meetings, _meeting(
        decisions=[{"id": "D1", "decision": "仅主持词无角色意见", "owner": ""}],
        transcript=[
            {"by": "HOST", "text": "主持词", "src": "host"},
            {"by": "unknown", "text": "无效发言", "src": "script"},
        ]))
    _write_council(council, [])
    payload = hd.build_directives(meetings, council, tmp_path / "tasks.json")
    dec = payload["directives"][0]
    assert dec["opinions"] == []
    assert dec["roles_present"] == []
    assert dec["roles_missing"] == list(hd.ROLE_ORDER)


# ---------- 显式任务链接 / 不猜 ----------
def test_explicit_task_link_and_unresolved(tmp_path):
    meetings = tmp_path / "meetings"
    council = tmp_path / "council"
    tasks = tmp_path / "tasks.json"
    _write_meeting(meetings, _meeting(
        decisions=[{"id": "D1", "decision": "链接真实任务", "owner": "GUANYU",
                    "task_id": "T-OK", "due": ""},
                   {"id": "D2", "decision": "链接不存在任务", "owner": "GUANYU",
                    "task_id": "T-MISSING", "due": ""}]))
    _write_council(council, [])
    _write_tasks(tasks, {"T-OK": {"status": "done", "owner": "GUANYU", "desc": "ok"}})
    payload = hd.build_directives(meetings, council, tasks)
    by_id = {d["id"]: d for d in payload["directives"]}
    ok = by_id["DIR-MEET-hq-20261103-060000-D1"]
    assert ok["task_id"] == "T-OK"
    assert ok["task_ref"]["task_exists"] is True
    assert any("task:T-OK" in e for e in ok["evidence"])
    missing = by_id["DIR-MEET-hq-20261103-060000-D2"]
    assert missing["task_id"] == ""
    assert missing["task_ref"]["source"] == "unresolved"
    assert any(w.startswith("task_link_unresolved:") for w in payload["warnings"])


# ---------- fail-visible / 坏文件 ----------
def test_no_source_data_fail_visible(tmp_path):
    empty_m = tmp_path / "mm"
    empty_c = tmp_path / "cc"
    empty_m.mkdir()
    empty_c.mkdir()
    payload = hd.build_directives(empty_m, empty_c, tmp_path / "tasks.json")
    assert payload["ok"] is False
    assert payload["error"] == "no_source_data"
    assert payload["directives"] == []
    rc, out, err = _run(["--json", "--meetings-dir", str(empty_m),
                         "--council-dir", str(empty_c),
                         "--tasks-file", str(tmp_path / "tasks.json")])
    assert rc == 2, (rc, out, err)
    body = json.loads(out)
    assert body["ok"] is False and body["error"] == "no_source_data"


def test_corrupt_meeting_skipped_and_warned(tmp_path):
    meetings = tmp_path / "meetings"
    council = tmp_path / "council"
    meetings.mkdir()
    (meetings / "hq-20261103-000000.json").write_text("{bad json", encoding="utf-8")
    _write_meeting(meetings, _meeting(
        decisions=[{"id": "D1", "decision": "有效决议", "owner": "A0", "due": ""}]))
    _write_council(council, [])
    payload = hd.build_directives(meetings, council, tmp_path / "tasks.json")
    assert payload["ok"] is True
    assert any(w.startswith("meeting_unreadable:") for w in payload["warnings"])
    assert len(payload["directives"]) == 1


# ---------- CLI JSON 纯度 / 过滤 ----------
def test_cli_json_pure_and_filters():
    rc, out, err = _run(["--json", "--kind", "decision", "--limit", "2"])
    assert rc == 0, (rc, out, err)
    payload = json.loads(out)  # stdout 必须纯 JSON
    assert payload["ok"] is True
    assert payload["filtered"]["kind"] == "decision"
    assert len(payload["directives"]) <= 2
    assert all(d["kind"] == "decision" for d in payload["directives"])


def test_filter_directives_function():
    rows = [
        {"id": "a", "kind": "decision", "status": "open", "meeting_id": "m1", "legion": "L1"},
        {"id": "b", "kind": "order", "status": "dispatched", "meeting_id": "m1", "legion": "L2"},
        {"id": "c", "kind": "topic", "status": "open", "meeting_id": "", "legion": "L1"},
    ]
    assert [d["id"] for d in hd.filter_directives(rows, kind="decision")] == ["a"]
    assert [d["id"] for d in hd.filter_directives(rows, status="open")] == ["a", "c"]
    assert [d["id"] for d in hd.filter_directives(rows, meeting="m1")] == ["a", "b"]
    assert [d["id"] for d in hd.filter_directives(rows, legion="L1")] == ["a", "c"]
    assert len(hd.filter_directives(rows, limit=1)) == 1
