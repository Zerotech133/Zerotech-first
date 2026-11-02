# -*- coding: utf-8 -*-
"""HQ LegionReport 只读模型测试（C3，task-33）。

覆盖：真实任务板冒烟、green/amber/red 状态规则、无任务 scope 防假绿、blockers 原因、
eta 来自 Directive due、owner/legion 过滤、JSON 纯度、无/坏任务板 fail-visible。
全部只读；合成任务写入临时目录。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
TOOL = ROOT / "tools" / "hq_reports.py"

spec = importlib.util.spec_from_file_location("hq_reports_under_test", TOOL)
hr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hr)


def _run(args, cwd=ROOT):
    p = subprocess.run([PY, "-X", "utf8", str(TOOL), *args], cwd=str(cwd),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120)
    return p.returncode, p.stdout or "", p.stderr or ""


def _write_tasks(path: Path, tasks: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tasks, ensure_ascii=False), encoding="utf-8")


def _write_meeting(dirpath: Path, decisions: list) -> None:
    dirpath.mkdir(parents=True, exist_ok=True)
    doc = {"schema_version": "2.0", "meeting_id": "hq-20261103-060000",
           "created_at": "2026-11-03T06:00:00", "type": "morning",
           "agenda": [], "attendees": ["A0"], "transcript": [],
           "decisions": decisions, "orders": [], "status": "decided"}
    (dirpath / "hq-20261103-060000.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def _task(status: str, owner: str, created: str = "2026-11-01 10:00:00",
          desc: str = "", **kw) -> dict:
    row = {"status": status, "owner": owner, "created": created, "desc": desc,
           "branch": "task/x", "worktree": "x"}
    row.update(kw)
    return row


def _synthetic_tasks() -> dict:
    return {
        "A1": _task("done", "ACAD1", desc="done-1"),
        "A2": _task("done", "ACAD1", desc="done-2"),
        "A3": _task("done", "ACAD1", desc="done-3"),
        "D1": _task("doing", "DATA1", desc="正在做"),
        "D2": _task("done", "DATA1", desc="done"),
        "G1": _task("aborted", "GUANYU", desc="失败单1", abort_reason="零成果空转29h"),
        "G2": _task("aborted", "GUANYU", desc="失败单2", abort_reason="阻塞:依赖未就绪"),
        "G3": _task("done", "GUANYU", desc="done-1"),
        "G4": _task("done", "GUANYU", desc="done-2"),
        "G5": _task("done", "GUANYU", desc="done-3"),
        "Z1": _task("done", "ZZZZ", desc="未映射 owner"),
        "P1": _task("parked", "PARK1", desc="停放单", park_reason="阻塞:等上游"),
    }


def _reports_for(tmp_path: Path, tasks: dict, with_directives: bool = True) -> dict:
    tasks_file = tmp_path / "tasks.json"
    _write_tasks(tasks_file, tasks)
    if not with_directives:
        return hr.build_reports(tasks_file)
    meetings = tmp_path / "meetings"
    _write_meeting(meetings, [{"id": "D1", "decision": "ACAD1 契约完成", "owner": "ACAD1",
                               "due": "2026-11-10", "evidence": [], "dissent": []}])
    council = tmp_path / "council"
    council.mkdir(exist_ok=True)
    (council / "council_new_topics.json").write_text("[]", encoding="utf-8")
    return hr.build_reports(tasks_file, meetings, council)


# ---------- 真实数据 ----------
def test_real_tasks_reports_smoke():
    payload = hr.build_reports()
    assert payload["ok"] is True, payload.get("error")
    assert payload["read_only"] is True
    assert payload["sources"]["tasks"] >= 1
    assert payload["summary"]["legions"] >= 1
    for row in payload["legions"]:
        for key in ("scope", "tasks_total", "tasks_done", "tasks_active",
                    "tasks_parked", "tasks_aborted", "progress", "status",
                    "status_reason", "eta", "blockers", "evidence"):
            assert key in row, (row.get("scope"), key)
        assert row["status"] in ("green", "amber", "red")
        assert 0.0 <= row["progress"] <= 100.0
        assert row["evidence"]
    assert payload["owners"], "真实任务板应有 owner 聚合"


# ---------- 状态规则（合成边界） ----------
def test_status_rules_synthetic(tmp_path):
    payload = _reports_for(tmp_path, _synthetic_tasks())
    assert payload["ok"] is True
    rows = {r["scope"]["legion"]: r for r in payload["legions"]}
    assert rows["acad1-legion"]["status"] == "green"
    assert rows["acad1-legion"]["progress"] == 100.0
    assert rows["data-warehouse"]["status"] == "amber"
    assert rows["data-warehouse"]["progress"] == 50.0
    assert rows["hq-audit"]["status"] == "red"          # GUANYU 2/5 aborted = 40%
    assert rows["hq-audit"]["aborted_ratio"] == 0.4
    assert "unmapped" in rows                            # ZZZZ 未映射 owner
    # 无任务 scope 必须 amber（不许假绿）
    assert rows["huangzhong-acceptance"]["status"] == "amber"
    assert rows["huangzhong-acceptance"]["status_reason"] == "no_tasks_in_scope"
    assert rows["huangzhong-acceptance"]["progress"] == 0.0


def test_metrics_no_data_scope_not_green():
    tasks = {"A1": _task("done", "ACAD1")}
    m = hr._metrics(tasks, owner="NOBODY")
    assert m["status"] == "amber"
    assert m["status_reason"] == "no_tasks_in_scope"
    assert m["tasks_total"] == 0


def test_blockers_include_reasons(tmp_path):
    payload = _reports_for(tmp_path, _synthetic_tasks())
    rows = {r["scope"]["legion"]: r for r in payload["legions"]}
    audit = rows["hq-audit"]
    assert audit["blockers_count"] >= 2
    reasons = " ".join(b["reason"] for b in audit["blockers"])
    assert "零成果空转" in reasons
    unmapped = rows["unmapped"]
    blocker_ids = {b["task_id"] for b in unmapped["blockers"]}
    assert "P1" in blocker_ids                            # parked + 阻塞 marker
    assert any("阻塞" in b["reason"] for b in unmapped["blockers"])


def test_eta_from_directive_due(tmp_path):
    payload = _reports_for(tmp_path, _synthetic_tasks())
    rows = {r["scope"]["legion"]: r for r in payload["legions"]}
    assert rows["acad1-legion"]["eta"] == "2026-11-10"
    assert rows["acad1-legion"]["directives_open"] >= 1
    owners = {r["scope"]["owner"]: r for r in payload["owners"]}
    assert owners["ACAD1"]["eta"] == "2026-11-10"
    assert owners["DATA1"]["eta"] is None


# ---------- 过滤 / JSON 纯度 ----------
def test_cli_json_pure_and_owner_filter(tmp_path):
    tasks_file = tmp_path / "tasks.json"
    _write_tasks(tasks_file, _synthetic_tasks())
    rc, out, err = _run(["--json", "--tasks-file", str(tasks_file), "--owner", "GUANYU"])
    assert rc == 0, (rc, out, err)
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["filtered"]["owner"] == "GUANYU"
    assert payload["owners"]
    assert all(r["scope"]["owner"] == "GUANYU" for r in payload["owners"])


def test_cli_legion_filter_and_limit_blockers(tmp_path):
    tasks_file = tmp_path / "tasks.json"
    _write_tasks(tasks_file, _synthetic_tasks())
    rc, out, err = _run(["--json", "--tasks-file", str(tasks_file), "--legion",
                         "acad1-legion,data-warehouse", "--limit-blockers", "1"])
    assert rc == 0, (rc, out, err)
    payload = json.loads(out)
    legions = {r["scope"]["legion"] for r in payload["legions"]}
    assert legions == {"acad1-legion", "data-warehouse"}
    assert all(r["blockers_count"] <= 1 for r in payload["legions"])


# ---------- fail-visible ----------
def test_missing_tasks_fail_visible(tmp_path):
    missing = tmp_path / "nope.json"
    rc, out, err = _run(["--json", "--tasks-file", str(missing)])
    assert rc == 2, (rc, out, err)
    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["error"] == "no_tasks_data"
    assert any("tasks_file_missing" in w for w in payload["warnings"])


def test_invalid_tasks_json_fail_visible(tmp_path):
    bad = tmp_path / "tasks.json"
    bad.write_text("{not json", encoding="utf-8")
    payload = hr.build_reports(bad)
    assert payload["ok"] is False
    assert payload["error"] == "no_tasks_data"
    rc, out, _ = _run(["--json", "--tasks-file", str(bad)])
    assert rc == 2
    assert json.loads(out)["ok"] is False


def test_summary_and_schema(tmp_path):
    payload = _reports_for(tmp_path, _synthetic_tasks())
    assert payload["schema"] == "hq/legion_reports/v1"
    summary = payload["summary"]
    assert summary["status_counts"]["red"] >= 1
    assert summary["tasks_total"] == len(_synthetic_tasks())
    assert summary["owners"] == len(payload["owners"])
