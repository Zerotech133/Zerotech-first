# -*- coding: utf-8 -*-
"""L5 量化资料注入单测（fake grounding/subprocess，不联网）。"""
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

_PORTAL = Path(__file__).resolve().parents[1] / "portal"
sys.path.insert(0, str(_PORTAL))


def _load():
    spec = importlib.util.spec_from_file_location("comm_zhuge_q_test",
                                                  _PORTAL / "comm_zhuge.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeGrounding:
    def build_grounding(self, q):
        return {"sources": [{"id": "S1", "path": "docs/x.md", "line": 1,
                             "snippet": "本地资料"}],
                "context": "[S1] docs/x.md:1 — 本地资料",
                "memory": [], "memory_context": "", "citations": [], "warnings": []}


def _fake_run(cmd, **kwargs):
    payload = {"ok": True, "rows": [
        {"trade_date": "20261027", "close": 10.5, "pct_chg": 1.2, "vol": 1000}]}
    return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")


def test_quant_source_injected(monkeypatch):
    c = _load()
    monkeypatch.setattr(c, "_grounding_module", lambda: _FakeGrounding())
    monkeypatch.setattr(c.subprocess, "run", _fake_run)
    g = c._build_grounding("000001.SZ 最近日线走势如何？")
    assert g is not None
    ids = [s["id"] for s in g["sources"]]
    assert "S2" in ids
    assert any("tushare:daily:000001.SZ" in s["path"] for s in g["sources"])
    assert "tushare:daily:000001.SZ" in g["context"]
    assert "20261027" in g["context"]


def test_quant_off_by_env(monkeypatch):
    c = _load()
    monkeypatch.setenv("COMM_ZHUGE_QUANT", "0")
    monkeypatch.setattr(c, "_grounding_module", lambda: _FakeGrounding())
    g = c._build_grounding("000001.SZ 行情")
    assert len(g["sources"]) == 1


def test_quant_no_code_no_inject(monkeypatch):
    c = _load()
    monkeypatch.setattr(c, "_grounding_module", lambda: _FakeGrounding())
    g = c._build_grounding("今天大盘怎么样")
    assert len(g["sources"]) == 1


def test_quant_failure_does_not_break(monkeypatch):
    c = _load()
    monkeypatch.setattr(c, "_grounding_module", lambda: _FakeGrounding())

    def boom(cmd, **kwargs):
        raise OSError("no py312")
    monkeypatch.setattr(c.subprocess, "run", boom)
    g = c._build_grounding("000001.SZ 行情")
    assert g is not None and len(g["sources"]) == 1
