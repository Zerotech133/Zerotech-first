# -*- coding: utf-8 -*-
"""C6 归因修复映射 + devtask 署名强制单测（2026-10-29）。"""
import importlib.util
import json
import os
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, TOOLS / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


c6 = _load("c6_attr_t", "c6_daily_kpi.py")
dev = _load("devtask_attr_t", "devtask.py")


def _event(**kw):
    e = {"ts": "2026-10-29 09:00:00", "day": "2026-10-29", "kind": "task_start",
         "task": "T1", "agent": "devtask",
         "detail": "attribution_missing=1 · owner_degraded=1 owner=unknown-agent"}
    e.update(kw)
    return e


def test_c6_fingerprint_matches_repair():
    e = _event()
    assert c6._fingerprint(e) == __import__("hashlib").sha1(
        "|".join(str(e.get(k) or "") for k in ("ts", "kind", "task", "detail")).encode("utf-8")
    ).hexdigest()[:16]


def test_c6_compute_uses_repairs(tmp_path, monkeypatch):
    ev = tmp_path / "events.jsonl"
    ev.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in
                            [_event(task="T1"), _event(task="T2")]), encoding="utf-8")
    tasks = tmp_path / "tasks.json"
    tasks.write_text("{}", encoding="utf-8")
    rep = tmp_path / "repairs.jsonl"
    first = _event(task="T1")
    rep.write_text(json.dumps({"fingerprint": c6._fingerprint(first), "agent": "DEV6"}) + "\n",
                   encoding="utf-8")
    monkeypatch.setattr(c6, "EVENTS", str(ev))
    monkeypatch.setattr(c6, "TASKS", str(tasks))
    monkeypatch.setattr(c6, "REPAIRS", str(rep))
    m = c6.compute(day=date(2026, 10, 29), days=1)["metrics"]
    assert m["task_start_total"] == 2
    assert m["attribution_repaired"] == 1
    assert m["attribution_missing"] == 1
    assert m["attribution_missing_raw"] == 2
    assert m["ownership_rate"] == 0.5


def test_c6_without_repairs(tmp_path, monkeypatch):
    ev = tmp_path / "events.jsonl"
    ev.write_text(json.dumps(_event(), ensure_ascii=False), encoding="utf-8")
    tasks = tmp_path / "tasks.json"
    tasks.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(c6, "EVENTS", str(ev))
    monkeypatch.setattr(c6, "TASKS", str(tasks))
    monkeypatch.setattr(c6, "REPAIRS", str(tmp_path / "none.jsonl"))
    m = c6.compute(day=date(2026, 10, 29), days=1)["metrics"]
    assert m["attribution_repaired"] == 0 and m["ownership_rate"] == 0.0


def test_devtask_attribution_gate(monkeypatch):
    monkeypatch.delenv("AGENT_NAME", raising=False)
    monkeypatch.delenv("DEVTOOL_AGENT", raising=False)
    rc, argv = dev._attribution_gate(["new", "T1", "desc"])
    assert rc == 2 and argv == ["new", "T1", "desc"]
    rc, argv = dev._attribution_gate(["new", "T1", "desc", "--allow-unattributed"])
    assert rc == 0 and "--allow-unattributed" not in argv
    monkeypatch.setenv("AGENT_NAME", "DSH-Lead")
    rc, argv = dev._attribution_gate(["new", "T1", "desc"])
    assert rc == 0


def test_devtask_attribution_agent_fallback(monkeypatch):
    monkeypatch.delenv("AGENT_NAME", raising=False)
    monkeypatch.delenv("DEVTOOL_AGENT", raising=False)
    assert dev._attribution_agent() == "devtask"
    monkeypatch.setenv("AGENT_NAME", "ACAD1")
    assert dev._attribution_agent() == "ACAD1"
    monkeypatch.setenv("DEVTOOL_AGENT", "DEV6")
    assert dev._attribution_agent() == "ACAD1"
