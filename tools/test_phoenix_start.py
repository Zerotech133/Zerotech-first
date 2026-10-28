# -*- coding: utf-8 -*-
"""Phoenix 自启幂等化单测（不联网、不真起进程）。"""
import importlib.util
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("phoenix_start",
                                                  _TOOLS / "phoenix_start.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_already_running(monkeypatch):
    p = _load()
    spawned = []
    rep = p.ensure_running(health=lambda: True,
                           spawner=lambda c, l: spawned.append(c))
    assert rep["ok"] is True and rep["action"] == "already_running"
    assert spawned == []


def test_starts_when_down(monkeypatch, tmp_path):
    p = _load()
    fake_exe = tmp_path / "phoenix.exe"
    fake_exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(p, "PHOENIX", fake_exe)
    calls = {"n": 0}

    def health():
        calls["n"] += 1
        return calls["n"] > 1

    spawned = []
    rep = p.ensure_running(health=health,
                           spawner=lambda c, l: spawned.append(c),
                           wait_s=5, log_path=tmp_path / "x.log")
    assert rep["ok"] is True and rep["action"] == "started"
    assert spawned and "serve" in spawned[0]


def test_spawn_failure(monkeypatch, tmp_path):
    p = _load()
    fake_exe = tmp_path / "phoenix.exe"
    fake_exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(p, "PHOENIX", fake_exe)

    def boom(cmd, log):
        raise OSError("no")

    rep = p.ensure_running(health=lambda: False, spawner=boom,
                           log_path=tmp_path / "x.log")
    assert rep["ok"] is False and rep["action"] == "spawn_failed"


def test_missing_exe(monkeypatch, tmp_path):
    p = _load()
    monkeypatch.setattr(p, "PHOENIX", tmp_path / "nope.exe")
    rep = p.ensure_running(health=lambda: False,
                           spawner=lambda c, l: None)
    assert rep["ok"] is False and rep["action"] == "missing_exe"


def test_unhealthy_after_wait(monkeypatch, tmp_path):
    p = _load()
    fake_exe = tmp_path / "phoenix.exe"
    fake_exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(p, "PHOENIX", fake_exe)
    rep = p.ensure_running(health=lambda: False,
                           spawner=lambda c, l: None,
                           wait_s=1, log_path=tmp_path / "x.log")
    assert rep["ok"] is False and rep["action"] == "started_but_unhealthy"
