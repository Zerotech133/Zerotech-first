# -*- coding: utf-8 -*-
"""Merge 窗口批处理测试（2026-11-16 WO-MERGE-WINDOW）。

只测窗口判定与拒绝/演示分支，绝不真的调 devtask merge。
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _load():
    spec = importlib.util.spec_from_file_location(
        "merge_window_run_t", REPO / "tools" / "merge_window_run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mw = _load()


def _at(hh: int, mm: int) -> dt.datetime:
    return dt.datetime(2026, 11, 16, hh, mm, 0)


def test_in_window_boundaries():
    assert mw.in_window(_at(16, 29))[0] is False
    assert mw.in_window(_at(16, 30))[0] is True
    assert mw.in_window(_at(17, 0))[0] is True
    assert mw.in_window(_at(17, 1))[0] is False
    assert mw.in_window(_at(21, 39))[0] is False
    assert mw.in_window(_at(21, 40))[0] is True
    assert mw.in_window(_at(22, 10))[0] is True
    assert mw.in_window(_at(22, 11))[0] is False


def test_in_window_reason_mentions_both_windows():
    ok, why = mw.in_window(_at(12, 0))
    assert ok is False and "16:30-17:00" in why and "21:40-22:10" in why


def test_dry_run_ok_outside_window(monkeypatch):
    monkeypatch.setattr(mw, "_now", lambda: _at(5, 15))
    monkeypatch.setattr(mw, "LOG", Path("nul") if sys.platform == "win32" else Path("/dev/null"))
    assert mw.main(["--tids", "A,B", "--dry-run"]) == 0


def test_refuse_outside_window(monkeypatch):
    monkeypatch.setattr(mw, "_now", lambda: _at(5, 15))
    monkeypatch.setattr(mw, "LOG", Path("nul") if sys.platform == "win32" else Path("/dev/null"))
    assert mw.main(["--tids", "A"]) == 2  # 窗口外拒绝，不改 master


def test_force_bypasses_window_but_marks(monkeypatch, tmp_path):
    """--force 允许窗口外，但要留痕（forced=True）。用假 run_merge 避免真合并。"""
    calls = []

    def fake_run_merge(tid, timeout):
        calls.append(tid)
        return {"tid": tid, "rc": 0, "seconds": 0.1, "tail": "fake", "ok": True}

    monkeypatch.setattr(mw, "_now", lambda: _at(5, 15))
    monkeypatch.setattr(mw, "LOG", tmp_path / "log.txt")
    monkeypatch.setattr(mw, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(mw, "run_merge", fake_run_merge)
    rc = mw.main(["--tids", "A", "--force"])
    assert rc == 0 and calls == ["A"]
    files = list((tmp_path / "out").rglob("merge_*.json"))
    assert len(files) == 1
    import json
    doc = json.loads(files[0].read_text(encoding="utf-8"))
    assert doc["forced"] is True and doc["ok"] is True


def test_inside_window_runs_merges(monkeypatch, tmp_path):
    calls = []

    def fake_run_merge(tid, timeout):
        calls.append(tid)
        return {"tid": tid, "rc": 1, "seconds": 0.1, "tail": "boom", "ok": False}

    monkeypatch.setattr(mw, "_now", lambda: _at(16, 31))
    monkeypatch.setattr(mw, "LOG", tmp_path / "log.txt")
    monkeypatch.setattr(mw, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(mw, "run_merge", fake_run_merge)
    rc = mw.main(["--tids", "A,B"])
    assert rc == 1 and calls == ["A", "B"]  # 一条失败不影响下一条，整体 rc=1
