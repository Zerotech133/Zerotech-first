# -*- coding: utf-8 -*-
"""O6 P2a：trace.run_id 透传/迁移单测。"""
from __future__ import annotations

import importlib.util
import re
import sqlite3
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "trace_run_id", str(_WT / "agent_platform" / "observability" / "trace.py"))
TR = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(TR)


def _db(tmp_path, monkeypatch):
    db = tmp_path / "traces.sqlite"
    monkeypatch.setattr(TR, "DB", db)
    return db


def _col(db):
    con = sqlite3.connect(str(db))
    try:
        return [r[1] for r in con.execute("PRAGMA table_info(spans)").fetchall()]
    finally:
        con.close()


def test_run_scope_writes_run_id(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    tid = TR.trace_id()
    with TR.run_scope("run_test_1"):
        TR.record(tid, "x", "tool")
        with TR.span("s", "tool", trace=tid) as sp:
            sp.set("a", 1)
    con = sqlite3.connect(str(db))
    rows = con.execute("select run_id from spans where trace_id=?", (tid,)).fetchall()
    con.close()
    assert len(rows) == 2
    assert all(r[0] == "run_test_1" for r in rows)


def test_no_scope_writes_null(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    tid = TR.trace_id()
    TR.record(tid, "x", "tool")
    con = sqlite3.connect(str(db))
    row = con.execute("select run_id from spans where trace_id=?", (tid,)).fetchone()
    con.close()
    assert row[0] is None


def test_env_off_disables(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    monkeypatch.setenv("AIOS_RUN_ID", "off")
    assert TR.current_run_id() is None
    with TR.run_scope() as rid:
        assert rid.startswith("run_")
        assert TR.current_run_id() is None
        TR.record(TR.trace_id(), "x", "tool")
    con = sqlite3.connect(str(db))
    row = con.execute("select run_id from spans").fetchone()
    con.close()
    assert row[0] is None


def test_nested_scopes_restore():
    with TR.run_scope("run_outer"):
        assert TR.current_run_id() == "run_outer"
        with TR.run_scope("run_inner"):
            assert TR.current_run_id() == "run_inner"
        assert TR.current_run_id() == "run_outer"
    assert TR.current_run_id() is None


def test_explicit_override(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    tid = TR.trace_id()
    with TR.run_scope("run_ctx"):
        TR.record(tid, "x", "tool", run_id="run_explicit")
    con = sqlite3.connect(str(db))
    row = con.execute("select run_id from spans where trace_id=?", (tid,)).fetchone()
    con.close()
    assert row[0] == "run_explicit"


def test_migration_from_old_schema(tmp_path, monkeypatch):
    db = tmp_path / "old.sqlite"
    con = sqlite3.connect(str(db))
    con.execute("""CREATE TABLE spans(
        trace_id TEXT NOT NULL, span_id TEXT NOT NULL, parent_span_id TEXT,
        name TEXT NOT NULL, kind TEXT NOT NULL,
        start_ts TEXT NOT NULL, end_ts TEXT, status TEXT,
        attrs_json TEXT, created_at TEXT NOT NULL,
        PRIMARY KEY (trace_id, span_id))""")
    con.commit()
    con.close()
    monkeypatch.setattr(TR, "DB", db)
    tid = TR.trace_id()
    with TR.run_scope("run_mig"):
        TR.record(tid, "x", "tool")
    assert "run_id" in _col(db)
    con = sqlite3.connect(str(db))
    row = con.execute("select run_id from spans where trace_id=?", (tid,)).fetchone()
    con.close()
    assert row[0] == "run_mig"


def test_new_run_id_and_get_trace(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    rid = TR.new_run_id()
    assert re.match(r"^run_\d{12}[0-9a-f]{12}$", rid)
    tid = TR.trace_id()
    with TR.run_scope(rid):
        TR.record(tid, "x", "tool")
    got = TR.get_trace(tid)
    assert got["ok"] and got["spans"][0]["run_id"] == rid
