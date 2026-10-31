# -*- coding: utf-8 -*-
"""shadow_node_probe 单节点 smoke 的离线契约测试（不触网/不写账本）。"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "tools" / "shadow_node_probe.py"


def _load_probe():
    spec = importlib.util.spec_from_file_location("_shadow_node_probe", PROBE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_probe_single_node_ok(monkeypatch, capsys):
    probe = _load_probe()

    class FakeLLM:
        def __init__(self, timeout=None):
            self.timeout = timeout

        def chat(self, system, user, model):
            assert "T1" or model
            return "OK", "normal"

    monkeypatch.setattr(probe, "OllamaLLM", FakeLLM)
    rc = probe.main(["--node", "T1", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["results"][0]["node"] == "t1_market"
    assert out["results"][0]["status"] == "normal"


def test_probe_unknown_node_is_fail_visible(capsys):
    probe = _load_probe()
    rc = probe.main(["--node", "NOPE"])
    assert rc == 2
    assert "未知节点" in capsys.readouterr().err


def test_probe_error_returns_nonzero(monkeypatch, capsys):
    probe = _load_probe()

    class BadLLM:
        def __init__(self, timeout=None):
            pass

        def chat(self, system, user, model):
            raise RuntimeError("boom")

    monkeypatch.setattr(probe, "OllamaLLM", BadLLM)
    rc = probe.main(["--node", "T1", "--json"])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert "boom" in out["results"][0]["error"]
