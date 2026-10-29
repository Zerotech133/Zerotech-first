# -*- coding: utf-8 -*-
"""归因缺口修复映射单测（2026-10-29）。"""
import importlib.util
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
TOOLS = HERE.parent
spec = importlib.util.spec_from_file_location("attr_repair_t", TOOLS / "attribution_repair.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def _e(**kw):
    e = {"ts": "2026-10-27 11:04:45", "day": "2026-10-27", "kind": "task_start",
         "task": "T1", "agent": "devtask",
         "detail": "attribution_missing=1 · owner_degraded=1 owner=unknown-agent"}
    e.update(kw)
    return e


def test_fingerprint_stable():
    assert m.fingerprint(_e()) == m.fingerprint(_e())


def test_resolve_event_agent_first():
    a, src = m.resolve_event(_e(agent="DSH-Lead"), {})
    assert (a, src) == ("DSH-Lead", "event-agent")


def test_resolve_event_task_owner():
    a, src = m.resolve_event(_e(), {"T1": {"owner": "DEV6"}})
    assert (a, src) == ("DEV6", "task-owner")


def test_resolve_event_detail_owner():
    a, src = m.resolve_event(_e(detail="attribution_missing=1 · --owner INTEL0"), {})
    assert (a, src) == ("INTEL0", "detail-owner")


def test_resolve_event_unresolved():
    a, src = m.resolve_event(_e(), {})
    assert a is None and src is None


def test_build_repairs():
    events = [_e(task="T1"), _e(task="T2", agent="DSH-Lead"),
              _e(task="T3", detail="attribution_missing=1 · --owner ACAD1"),
              {"kind": "task_start", "task": "T4", "detail": "no marker"},
              _e(task="T5")]
    resolved, unresolved = m.build_repairs(events, {"T1": {"owner": "DEV6"}})
    assert len(resolved) == 3 and len(unresolved) == 1
    srcs = {r["source"] for r in resolved}
    assert srcs == {"task-owner", "event-agent", "detail-owner"}


def test_main_write_and_strict(tmp_path, monkeypatch):
    ev = tmp_path / "events.jsonl"
    ev.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in
                            [_e(task="T1"), _e(task="T2")]), encoding="utf-8")
    tasks = tmp_path / "tasks.json"
    tasks.write_text(json.dumps({"T1": {"owner": "DEV6"}}), encoding="utf-8")
    out = tmp_path / "repairs.jsonl"
    monkeypatch.setattr(m, "EVENTS", str(ev))
    monkeypatch.setattr(m, "TASKS", str(tasks))
    monkeypatch.setattr(m, "OUT", str(out))
    assert m.main(["--write", "--json"]) == 0
    rows = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1 and rows[0]["agent"] == "DEV6"
    # 第二次 --write 不重复追加
    m.main(["--write"])
    assert len(out.read_text(encoding="utf-8").splitlines()) == 1
    assert m.main(["--strict"]) == 1  # T2 仍 unresolved
