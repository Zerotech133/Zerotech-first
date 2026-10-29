# -*- coding: utf-8 -*-
"""O6 P2d：run_contract_view 任务级聚合单测。"""
import importlib.util
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("rcv_p2d", HERE / "run_contract_view.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

OK = ("gen_ai.usage.input_tokens", 10)


def _row(tid, sid, name, kind, ts, status, attrs, run_id=None):
    payload = dict(attrs)
    row = (tid, sid, name, kind, ts, ts, status, json.dumps(payload, ensure_ascii=False))
    return row + (run_id,) if run_id is not None else row


def test_task_level_grouping():
    rows = [
        _row("t1", "s1", "llm.attempt", "llm", "2026-10-27T10:00:00+0800", "error",
             {"gen_ai.request.model": "m1", "error": "down"}, "runA"),
        _row("t2", "s2", "llm.attempt", "llm", "2026-10-27T10:00:01+0800", "error",
             {"gen_ai.request.model": "m2", "error": "down"}, "runA"),
        _row("t3", "s3", "llm.attempt", "llm", "2026-10-27T10:00:02+0800", "error",
             {"gen_ai.request.model": "m3", "error": "empty_reply"}, "runA"),
        _row("t4", "s4", "llm.attempt", "llm", "2026-10-27T10:00:03+0800", "ok",
             {"gen_ai.request.model": "m1"}, "runB"),
        _row("t5", "s5", "llm.attempt", "llm", "2026-10-27T10:00:04+0800", "error",
             {"gen_ai.request.model": "m2", "error": "429"}, "runB"),
    ]
    runs, events = rc.derive_rows(rows)
    assert len(runs) == 2 and len(events) == 5
    by_id = {r["run_id"]: r for r in runs}
    assert by_id["runA"]["status"] == "failed"
    assert by_id["runA"]["meta"]["attempts"] == 3
    assert by_id["runA"]["meta"]["level"] == "task"
    assert by_id["runB"]["status"] == "succeeded"
    assert by_id["runB"]["meta"]["attempts"] == 2
    assert events[0]["run_id"] == "runA" and events[0]["seq"] == 0
    assert events[1]["seq"] == 1


def test_attempt_level_fallback_without_run_id():
    rows = [
        _row("t1", "s1", "llm.call", "llm", "2026-10-27T10:00:00+0800", "ok", {}),
        _row("t2", "s2", "tool.read", "tool", "2026-10-27T10:00:01+0800", "error", {"error": "x"}),
    ]
    runs, events = rc.derive_rows(rows)
    assert len(runs) == 2
    assert all(r["meta"]["level"] == "attempt" for r in runs)
    assert {r["status"] for r in runs} == {"succeeded", "failed"}


def test_summary_rates(tmp_path):
    rows = [
        _row("t1", "s1", "llm.attempt", "llm", "2026-10-27T10:00:00+0800", "error", {"error": "x"}, "runA"),
        _row("t2", "s2", "llm.attempt", "llm", "2026-10-27T10:00:01+0800", "ok", {}, "runB"),
        _row("t3", "s3", "llm.attempt", "llm", "2026-10-27T10:00:02+0800", "ok", {}, "runB"),
    ]
    runs, events = rc.derive_rows(rows)
    s = rc.write_outputs(str(tmp_path), runs, events)
    assert s["task_runs"] == 2 and s["attempt_runs"] == 0
    assert s["task_success_rate"] == 0.5
    doc = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert doc["runs"] == 2 and doc["ok"] if "ok" in doc else True


def test_validate_task_level():
    rows = [_row("t1", "s1", "llm.attempt", "llm", "2026-10-27T10:00:00+0800", "ok", {}, "runA")]
    runs, events = rc.derive_rows(rows)
    assert rc.validate(runs, events) == []
