# -*- coding: utf-8 -*-
"""L5 辩论字段单测（fake 模块）。"""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

PORTAL = Path(__file__).resolve().parents[1] / "portal"
sys.path.insert(0, str(PORTAL))


def _load():
    spec = importlib.util.spec_from_file_location("comm_zhuge_db_test",
                                                  PORTAL / "comm_zhuge.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_debate_enabled(monkeypatch):
    c = _load()
    monkeypatch.setenv("COMM_ZHUGE_DEBATE", "1")
    monkeypatch.setattr(c, "_tool_module", lambda n: SimpleNamespace(
        debate=lambda q: {"ok": True, "kind": "debate_v0", "question": q}))
    rep = c._debate_for("加息")
    assert rep["ok"] is True and rep["kind"] == "debate_v0"


def test_debate_disabled(monkeypatch):
    c = _load()
    monkeypatch.setenv("COMM_ZHUGE_DEBATE", "0")
    assert c._debate_for("加息") is None


def test_debate_fail_open(monkeypatch):
    c = _load()
    monkeypatch.setenv("COMM_ZHUGE_DEBATE", "1")

    def boom(n):
        raise RuntimeError("x")
    monkeypatch.setattr(c, "_tool_module", boom)
    assert c._debate_for("x") is None
