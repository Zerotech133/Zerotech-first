# -*- coding: utf-8 -*-
"""进化账本只读投影单测（仿 test_api_tickets.py 判例）。

红线：测试绝不读真实 automation_runs/、data/org/tasks/、shadow_ledger——
tmp 目录注入，pytest -q 全绿。
运行：python -m pytest portal/test_api_evolution.py -q
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import api_evolution  # noqa: E402


def _alert(ts="2026-10-18T09:18:15", rc=2147946720, tid="T-20261018-013"):
    return {"last_alert_ts": ts, "error_code": rc, "ticket_id": tid}


def _task(tid="T-20261018-013", status="open"):
    return {
        "task_id": tid,
        "title": "计划任务失败必响：X",
        "status": status,
        "created_at": "2026-10-18T09:18:00",
    }


def _mk_watchdog(tmp_path, alerts):
    """alerts: {任务名: alert_rec}"""
    d = tmp_path / "execution_watchdog"
    d.mkdir(parents=True)
    for name, rec in alerts.items():
        (d / ("%s.json" % name)).write_text(
            json.dumps(rec, ensure_ascii=False), encoding="utf-8"
        )
    return d


def _mk_tasks_dir(tmp_path, recs, fname="tasks_202610.jsonl"):
    d = tmp_path / "org" / "tasks"
    d.mkdir(parents=True)
    (d / fname).write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n",
        encoding="utf-8",
    )
    return d


def _mk_shadow(tmp_path, recs, name="shadow_ledger.jsonl"):
    d = tmp_path / "shadow"
    d.mkdir(parents=True)
    (d / name).write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n",
        encoding="utf-8",
    )
    return d / name


class TestLoadEvolution:
    def test_full_aggregation(self, tmp_path):
        wd = _mk_watchdog(
            tmp_path,
            {
                "AI工作站门户API": _alert(
                    ts="2026-10-18T09:38:16", tid="T-20261018-014"
                ),
                "HermesWebUI-Guard": _alert(
                    ts="2026-10-18T09:18:15", tid="T-20261018-011"
                ),
            },
        )
        td = _mk_tasks_dir(
            tmp_path,
            [
                _task("T-20261018-014", status="done"),
                _task("T-20261018-011", status="doing"),
            ],
        )
        sp = _mk_shadow(
            tmp_path,
            [
                {
                    "date": "2026-10-08",
                    "ts": 1791442784.8,
                    "status": "done",
                    "run_id": "e1d80bc78cfb",
                },
                {
                    "date": "2026-10-09",
                    "ts": 1791500000.0,
                    "status": "pending",
                    "run_id": "aa0000000001",
                },
            ],
        )
        p = api_evolution.load_evolution(watchdog_dir=wd, tasks_dir=td, shadow_path=sp)
        assert p["ok"] is True and p["available"] is True
        ac = p["alert_closure"]
        assert ac["total"] == 2 and ac["closed"] == 1 and ac["open"] == 1
        assert ac["rate_pct"] == 50.0
        # recent 新在前，且带对齐后的工单状态
        assert ac["recent"][0]["task"] == "AI工作站门户API"
        assert ac["recent"][0]["status"] == "done"
        assert ac["recent"][1]["status"] == "doing"
        assert p["shadow"]["rows"] == 2
        assert p["shadow"]["by_verdict"] == {
            "pass": 1,
            "fail": 0,
            "pending": 1,
            "other": 0,
        }
        assert p["shadow"]["latest_date"] == "2026-10-09"
        assert p["shadow"]["latest_run_id"] == "aa0000000001"
        assert p["eval"] == {"available": False, "reason": "周五 17:45 首评后自动点亮"}

    def test_recent_capped_at_10(self, tmp_path):
        wd = _mk_watchdog(tmp_path, {})
        for i in range(12):
            rec = _alert(ts="2026-10-18T09:%02d:00" % i, tid="T-20261018-%03d" % i)
            (wd / ("任务%02d.json" % i)).write_text(
                json.dumps(rec, ensure_ascii=False), encoding="utf-8"
            )
        td = _mk_tasks_dir(tmp_path, [])
        p = api_evolution.load_evolution(
            watchdog_dir=wd, tasks_dir=td, shadow_path=tmp_path / "none.jsonl"
        )
        assert p["alert_closure"]["total"] == 12
        assert len(p["alert_closure"]["recent"]) == 10

    def test_last_run_json_skipped(self, tmp_path):
        wd = _mk_watchdog(tmp_path, {})
        (wd / "last_run.json").write_text(
            json.dumps(
                {
                    "ran_at": "2026-10-18T10:58:15",
                    "exit": 0,
                    "alerted": [],
                    "skipped_throttle": [],
                }
            ),
            encoding="utf-8",
        )
        p = api_evolution.load_evolution(
            watchdog_dir=wd,
            tasks_dir=_mk_tasks_dir(tmp_path, []),
            shadow_path=tmp_path / "none.jsonl",
        )
        assert p["alert_closure"]["total"] == 0

    def test_missing_dirs_fail_visible(self, tmp_path):
        p = api_evolution.load_evolution(
            watchdog_dir=tmp_path / "nope1",
            tasks_dir=tmp_path / "nope2",
            shadow_path=tmp_path / "nope3.jsonl",
        )
        assert p["ok"] is False and p["available"] is False
        assert len(p["errors"]) == 3
        assert "不存在" in p["errors"][0]
        assert p["alert_closure"]["total"] == 0
        assert p["shadow"]["rows"] == 0

    def test_empty_dirs_available_false(self, tmp_path):
        wd = _mk_watchdog(tmp_path, {})
        td = _mk_tasks_dir(tmp_path, [])
        sp = _mk_shadow(tmp_path, [])
        p = api_evolution.load_evolution(watchdog_dir=wd, tasks_dir=td, shadow_path=sp)
        assert p["ok"] is True and p["available"] is False
        assert p["alert_closure"]["rate_pct"] == 0.0

    def test_bad_lines_skipped(self, tmp_path):
        wd = _mk_watchdog(tmp_path, {"X": _alert()})
        td = tmp_path / "org" / "tasks"
        td.mkdir(parents=True)
        (td / "tasks_202610.jsonl").write_text(
            json.dumps(_task("T-20261018-013", status="done"), ensure_ascii=False)
            + "\n{broken json\n\n",
            encoding="utf-8",
        )
        sp = tmp_path / "shadow"
        sp.mkdir()
        (sp / "shadow_ledger.jsonl").write_text(
            json.dumps(
                {"date": "2026-10-08", "ts": 1.0, "status": "done", "run_id": "r1"},
                ensure_ascii=False,
            )
            + "\nnot json\n",
            encoding="utf-8",
        )
        p = api_evolution.load_evolution(
            watchdog_dir=wd, tasks_dir=td, shadow_path=sp / "shadow_ledger.jsonl"
        )
        assert p["ok"] is True
        assert p["alert_closure"]["closed"] == 1
        assert p["shadow"]["rows"] == 1

    def test_contract_fields(self, tmp_path):
        p = api_evolution.load_evolution(
            watchdog_dir=tmp_path / "a",
            tasks_dir=tmp_path / "b",
            shadow_path=tmp_path / "c.jsonl",
        )
        for k in ("ok", "available", "generated_at", "alert_closure", "shadow", "eval"):
            assert k in p
        for k in ("total", "closed", "open", "rate_pct", "recent"):
            assert k in p["alert_closure"]
        for k in ("rows", "by_verdict", "latest_date", "latest_run_id"):
            assert k in p["shadow"]
        assert set(p["shadow"]["by_verdict"]) == {"pass", "fail", "pending", "other"}

    def test_runner_file_shadow_fallback_path(self, tmp_path):
        """shadow_path 不传时兼容旧路径 data/shadow_ledger.jsonl。"""
        wd = _mk_watchdog(tmp_path, {"X": _alert()})
        old = tmp_path / "shadow_ledger.jsonl"  # 旧路径（shadow/ 目录外）
        old.write_text(
            json.dumps(
                {"date": "2026-10-08", "ts": 2.0, "status": "done", "run_id": "r2"},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        monkey_candidates = api_evolution._SHADOW_CANDIDATES
        api_evolution._SHADOW_CANDIDATES = (old,)
        try:
            p = api_evolution.load_evolution(
                watchdog_dir=wd, tasks_dir=_mk_tasks_dir(tmp_path, [])
            )
        finally:
            api_evolution._SHADOW_CANDIDATES = monkey_candidates
        assert p["shadow"]["rows"] == 1
        assert p["shadow"]["latest_run_id"] == "r2"
