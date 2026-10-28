# -*- coding: utf-8 -*-
"""O8 P3c 夜跑 dep_age 段单测（不联网）。"""
import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import night_test_baseline as N  # noqa: E402


def test_build_dep_age_command():
    cmd = N.build_dep_age_command(0)
    assert cmd[1:] == ["-X", "utf8", str(HERE / "dep_age_check.py"), "--json"]
    cmd2 = N.build_dep_age_command(7)
    assert cmd2[-2:] == ["--limit", "7"]


def test_summarize_dep_age():
    payload = {"checked": 10, "fresh_count": 2, "error_count": 1, "cutoff": "2026-10-21",
               "fresh": [{"name": "a", "version": "1", "age_days": 1.0},
                         {"name": "b", "version": "2", "age_days": 3.0}]}
    s = N.summarize_dep_age(payload)
    assert s["checked"] == 10
    assert s["fresh_count"] == 2
    assert s["error_count"] == 1
    assert s["fresh_top"][0]["name"] == "a"
    assert s["warn_only"] is True


def test_run_dep_age_parses_json(monkeypatch):
    payload = {"checked": 1, "fresh_count": 0, "error_count": 0, "cutoff": "x", "fresh": []}
    monkeypatch.setattr(N.subprocess, "run",
                        lambda *a, **k: type("P", (), {"stdout": __import__("json").dumps(payload),
                                                       "returncode": 0})())
    out = N.run_dep_age(limit=1)
    assert out["ok"] is True and out["checked"] == 1


def test_run_dep_age_fail_open(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no network")
    monkeypatch.setattr(N.subprocess, "run", boom)
    out = N.run_dep_age()
    assert out["ok"] is False and "no network" in out["error"]
