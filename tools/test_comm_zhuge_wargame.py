# -*- coding: utf-8 -*-
"""L5 兵棋字段单测（fake 模块，不联网）。"""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

PORTAL = Path(__file__).resolve().parents[1] / "portal"
sys.path.insert(0, str(PORTAL))


def _load():
    spec = importlib.util.spec_from_file_location("comm_zhuge_wg_test",
                                                  PORTAL / "comm_zhuge.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_mod():
    return SimpleNamespace(wargame=lambda q: {"ok": True, "kind": "wargame_v0",
                                              "question": q})


def test_wargame_field_when_enabled(monkeypatch):
    c = _load()
    monkeypatch.setenv("COMM_ZHUGE_WARGAME", "1")
    monkeypatch.setattr(c, "_tool_module", lambda name: _fake_mod())
    rep = c._wargame_for("加息周期")
    assert rep["ok"] is True and rep["kind"] == "wargame_v0"


def test_wargame_field_when_disabled(monkeypatch):
    c = _load()
    monkeypatch.setenv("COMM_ZHUGE_WARGAME", "0")
    assert c._wargame_for("加息周期") is None


def test_wargame_field_exception_fail_open(monkeypatch):
    c = _load()
    monkeypatch.setenv("COMM_ZHUGE_WARGAME", "1")

    def boom(name):
        raise RuntimeError("import fail")
    monkeypatch.setattr(c, "_tool_module", boom)
    assert c._wargame_for("x") is None
