# -*- coding: utf-8 -*-
"""O6 P2c：legion_command 请求级 run_scope 单测。"""
import sqlite3
import sys
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_WT / "agent_platform"))

import observability.trace as TR  # noqa: E402
import legion_command.serve as serve  # noqa: E402


def _fake_handler(impl_name, impl):
    h = object.__new__(serve.Handler)
    h.path = "/"
    setattr(h, impl_name, impl)
    return h


def test_do_get_has_run_scope(tmp_path, monkeypatch):
    db = tmp_path / "trace_p2c.sqlite"
    monkeypatch.setattr(TR, "DB", db)
    seen = []

    def impl():
        seen.append(TR.current_run_id())
        TR.record(TR.trace_id(), "tool.test", "tool")
        return "ok"

    h = _fake_handler("_do_GET_impl", impl)
    assert h.do_GET() == "ok"
    h2 = _fake_handler("_do_GET_impl", impl)
    h2.do_GET()
    assert len(seen) == 2
    assert all(x and str(x).startswith("run_") for x in seen)
    assert seen[0] != seen[1]  # 每个请求独立 run
    con = sqlite3.connect(str(db))
    rows = con.execute("select run_id from spans where name='tool.test'").fetchall()
    con.close()
    assert len(rows) == 2 and all(r[0] in seen for r in rows)


def test_do_post_has_run_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(TR, "DB", tmp_path / "trace_p2c2.sqlite")
    seen = []

    def impl():
        seen.append(TR.current_run_id())
        return "ok"

    h = _fake_handler("_do_POST_impl", impl)
    assert h.do_POST() == "ok"
    assert seen and str(seen[0]).startswith("run_")
    assert TR.current_run_id() is None  # 请求结束上下文已退出


def test_request_scope_isolated_per_call():
    seen = []
    h = object.__new__(serve.Handler)
    with h._request_scope():
        seen.append(TR.current_run_id())
    with h._request_scope():
        seen.append(TR.current_run_id())
    assert seen[0] != seen[1]
