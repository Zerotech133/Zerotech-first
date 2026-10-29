# -*- coding: utf-8 -*-
"""O6 P2b：engine Interpreter.run 的 run_scope + attempt 记录单测。"""
import importlib.util
import sqlite3
import sys
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_WT / "agent_platform"))
_spec = importlib.util.spec_from_file_location(
    "interpreter_p2b", str(_WT / "agent_platform" / "engine" / "interpreter.py"))
IT = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(IT)

import observability.trace as TR  # noqa: E402


class FakeTeam:
    dept = "test-dept"
    agents = [{"node_key": "n1", "agent_id": "a1", "level": 1,
               "llm_binding": "mock/model", "role_profile": "r", "analysis_type": "t"}]
    by_node = {}

    def prompt(self, node):
        return "p"


class FakeCP:
    def start_run(self, run_id, stock):
        self.started = (run_id, stock)

    def get_run_states(self, run_id):
        return {}

    def finish_run(self, run_id, status="done"):
        self.finished = (run_id, status)

    def save_node(self, *a, **k):
        pass

    def trace(self, *a, **k):
        pass


class FakeMemory:
    def write(self, *a, **k):
        pass


class FakeLLM:
    def chat(self, system, user, model=None):
        return "ok", "normal"


def _interp():
    return IT.Interpreter(FakeTeam(), FakeLLM(), FakeMemory(), FakeCP())


def _db(tmp_path, monkeypatch):
    db = tmp_path / "trace_p2b.sqlite"
    monkeypatch.setattr(TR, "DB", db)
    return db


def test_run_writes_run_id_on_attempt(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    out = _interp().run("600000", run_id="run_p2b_test")
    assert out["run_id"] == "run_p2b_test"
    con = sqlite3.connect(str(db))
    n, rid = con.execute(
        "select count(*), max(run_id) from spans where name='llm.attempt'").fetchone()
    con.close()
    assert n >= 1
    assert rid == "run_p2b_test"


def test_worker_thread_sees_run_id(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    it = _interp()
    seen = []

    def fake_dispatch(kind, na, nb, ctx, run_id):
        seen.append(TR.current_run_id())
        return [(na["node_key"], "ok", "normal")]

    it._dispatch = fake_dispatch
    out = it.run("600001", run_id="run_p2b_ctx")
    assert out["run_id"] == "run_p2b_ctx"
    assert seen == ["run_p2b_ctx"]


def test_run_without_trace_module_still_works(tmp_path, monkeypatch):
    # trace 模块不可用不应阻断主流程（nullcontext 降级）
    monkeypatch.setattr(IT.Interpreter, "_run_scope", lambda self, rid: __import__("contextlib").nullcontext())
    out = _interp().run("600002")
    assert out["run_id"]
    assert out["decision"] is not None or out["stock"] == "600002"
