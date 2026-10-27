"""Trace 存储测试（WO-TRACE-OTEL）。"""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "trace", str(_WT / "agent_platform" / "observability" / "trace.py")
)
TR = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(TR)


def _db(tmp_path, monkeypatch):
    db = tmp_path / "traces.sqlite"
    monkeypatch.setattr(TR, "DB", db)
    return db


def test_record_list_get(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    tid = TR.trace_id()
    r1 = TR.record(tid, "cap.drill", "tool", attrs={"zhaoyun.capability": "x"})
    assert r1["ok"]
    with TR.span(
        "approval", "approval", attrs={"zhaoyun.decision": "approve"}, trace=tid
    ) as sp:
        sp.set("zhaoyun.applied", True)
    lst = TR.list_traces()
    assert lst["ok"] and any(i["trace_id"] == tid for i in lst["items"])
    tr = TR.get_trace(tid)
    assert tr["ok"] and tr["count"] == 2
    names = {s["name"] for s in tr["spans"]}
    assert names == {"cap.drill", "approval"}


def test_cost_estimate(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    attrs = {
        "gen_ai.request.model": "deepseek/deepseek-flash",
        "gen_ai.usage.input_tokens": 1000000,
        "gen_ai.usage.output_tokens": 0,
    }
    assert TR.estimate_cost(attrs) == 0.5
    assert TR.estimate_cost({}) == 0.0


def test_kill_switch(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    monkeypatch.setenv("ZHAOYUN_TRACE", "off")
    r = TR.record(TR.trace_id(), "x", "tool")
    assert r.get("skipped") == "trace_off"


def test_retention(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    old = TR.trace_id()
    TR.record(
        old, "old", "tool", start=time.time() - 40 * 86400, end=time.time() - 40 * 86400
    )
    TR.record(TR.trace_id(), "new", "tool")
    rep = TR.retention(days=30, db=db)
    assert rep["ok"] and rep["deleted_old"] >= 1
    assert all(i["spans"] >= 1 for i in TR.list_traces()["items"])


def test_span_error_status(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    tid = TR.trace_id()
    try:
        with TR.span("boom", "tool", trace=tid):
            raise RuntimeError("x")
    except RuntimeError:
        pass
    tr = TR.get_trace(tid)
    assert tr["spans"][0]["status"] == "error"
