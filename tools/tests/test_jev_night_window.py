# -*- coding: utf-8 -*-
"""BATCH2 回归：夜跑跨午夜窗口、暂停旗 owner。"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import jev_night as jn  # noqa: E402


def _dt(h, m=0):
    return datetime(2026, 10, 31, h, m)


def test_window_inside_after_midnight_restart():
    # 01:00 重启：应立刻续跑到今天 07:00，而不是等 20.8h
    begin, end = jn.window("21:50", "07:00", now=_dt(1, 0))
    assert begin == _dt(1, 0)
    assert end == _dt(7, 0)


def test_window_inside_early_morning():
    begin, end = jn.window("21:50", "07:00", now=_dt(6, 0))
    assert begin == _dt(6, 0)
    assert end == _dt(7, 0)


def test_window_in_evening_uses_today_start():
    begin, end = jn.window("21:50", "07:00", now=_dt(23, 10))
    assert begin == _dt(23, 10)
    assert end == datetime(2026, 11, 1, 7, 0)


def test_window_before_start_waits_today_start():
    begin, end = jn.window("21:50", "07:00", now=_dt(20, 0))
    assert begin == _dt(21, 50)
    assert end == datetime(2026, 11, 1, 7, 0)


def test_pause_flag_owner_guard(tmp_path, monkeypatch):
    flag = tmp_path / "k266_m3.pause"
    monkeypatch.setattr(jn, "PAUSE_FILE", flag)
    monkeypatch.setattr(jn, "m3_pids", lambda: [])
    log = jn.Logger(str(tmp_path / "n.log"))
    assert jn.pause_m3(log, owner="jev_night") == 0
    data = json.loads(flag.read_text(encoding="utf-8"))
    assert data["owner"] == "jev_night" and data["pid"] > 0
    # 别的任务恢复：不删
    jn.resume_m3(log, owner="night_batch")
    assert flag.exists()
    # 本任务恢复：删除
    jn.resume_m3(log, owner="jev_night")
    assert not flag.exists()


def test_resume_legacy_text_flag_allowed(tmp_path, monkeypatch):
    flag = tmp_path / "k266_m3.pause"
    flag.write_text("paused by tools/jev_night.py at 2026-10-30T00:00:00\n", encoding="utf-8")
    monkeypatch.setattr(jn, "PAUSE_FILE", flag)
    log = jn.Logger(str(tmp_path / "n.log"))
    jn.resume_m3(log, owner="night_batch")
    assert not flag.exists()  # 旧版纯文本旗兼容删除
