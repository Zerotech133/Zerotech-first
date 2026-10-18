# -*- coding: utf-8 -*-
"""工单只读投影单测（驾驶舱一期，仿 test_execution_mixin.py 判例）。

红线：测试绝不读真实 data/org/tasks——tmp 目录注入。
运行：python -m pytest portal/test_api_tickets.py -q
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import api_tickets  # noqa: E402


def _rec(tid="T-20261018-001", status="doing", dept="dev",
         title="计划任务失败必响：X", severity="P0"):
    return {"task_id": tid, "opened_by": "system/execution-watchdog",
            "assigned_dept": dept, "title": title, "body": "长文不应投影",
            "severity": severity, "status": status,
            "created_at": "2026-10-18T09:18:00", "due": None,
            "history": [{"at": "2026-10-18T09:18:00", "action": "open"}]}


def _mk_tasks_dir(tmp_path, months):
    """months: {月文件名: [rec,...]}"""
    d = tmp_path / "org" / "tasks"
    d.mkdir(parents=True)
    for fname, recs in months.items():
        (d / fname).write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n",
            encoding="utf-8")
    return d


class TestLoadTickets:
    def test_basic_projection(self, tmp_path):
        d = _mk_tasks_dir(tmp_path, {"tasks_202610.jsonl": [_rec()]})
        rows, ok, err, meta = api_tickets.load_tickets(tasks_dir=d)
        assert ok and err is None and len(rows) == 1
        r = rows[0]
        assert r["task_id"] == "T-20261018-001" and r["status"] == "doing"
        assert "body" not in r and "history" not in r  # 白名单外不投影

    def test_newest_first_across_months(self, tmp_path):
        d = _mk_tasks_dir(tmp_path, {
            "tasks_202610.jsonl": [_rec("T-20261018-002")],
            "tasks_202609.jsonl": [_rec("T-20260930-001")],
        })
        rows, ok, _, _ = api_tickets.load_tickets(tasks_dir=d)
        assert ok and [r["task_id"] for r in rows] == [
            "T-20261018-002", "T-20260930-001"]

    def test_limit_stops_scan(self, tmp_path):
        recs = [_rec("T-20261018-%03d" % i) for i in range(1, 12)]
        d = _mk_tasks_dir(tmp_path, {"tasks_202610.jsonl": recs})
        rows, ok, _, meta = api_tickets.load_tickets(limit=5, tasks_dir=d)
        assert ok and len(rows) == 5
        assert meta["months_scanned"] == 1

    def test_bad_lines_skipped(self, tmp_path):
        d = tmp_path / "org" / "tasks"
        d.mkdir(parents=True)
        (d / "tasks_202610.jsonl").write_text(
            json.dumps(_rec(), ensure_ascii=False)
            + "\n{broken json\n\n", encoding="utf-8")
        rows, ok, _, meta = api_tickets.load_tickets(tasks_dir=d)
        assert ok and len(rows) == 1 and meta["skipped_bad"] == 1

    def test_missing_dir_fail_visible(self, tmp_path):
        rows, ok, err, _ = api_tickets.load_tickets(
            tasks_dir=tmp_path / "nope")
        assert ok is False and rows == [] and "不存在" in err

    def test_empty_month_file_ok(self, tmp_path):
        d = _mk_tasks_dir(tmp_path, {"tasks_202611.jsonl": []})
        rows, ok, err, _ = api_tickets.load_tickets(tasks_dir=d)
        assert ok and rows == [] and err is None


class TestPayload:
    def test_payload_shape_and_counts(self, tmp_path):
        d = _mk_tasks_dir(tmp_path, {"tasks_202610.jsonl": [
            _rec("T-1", status="doing"), _rec("T-2", status="open"),
            _rec("T-3", status="done")]})
        p = api_tickets.build_tickets_payload(tasks_dir=d)
        assert p["read_only"] is True and p["ok"] is True
        assert p["count"] == 3
        assert p["status_counts"] == {"open": 1, "doing": 1, "done": 1,
                                      "other": 0}
        assert p["generated_at"].startswith("2026-")

    def test_handler_dept_filter(self, tmp_path, monkeypatch):
        d = _mk_tasks_dir(tmp_path, {"tasks_202610.jsonl": [
            _rec("T-1", dept="dev"), _rec("T-2", dept="ops")]})
        monkeypatch.setattr(api_tickets, "TASKS_DIR", d)
        seen = {}

        class _Stub(api_tickets.Handler):
            def _json(self, obj, status=200):
                seen["payload"] = obj

        _Stub()._org_tasks("dept=dev")
        assert seen["payload"]["count"] == 1
        assert seen["payload"]["tasks"][0]["assigned_dept"] == "dev"
