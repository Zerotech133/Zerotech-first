# -*- coding: utf-8 -*-
"""watch_24h_probe liveness fallback regression tests."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import watch_24h_probe as probe  # noqa: E402


def test_psutil_process_is_authoritative(monkeypatch):
    monkeypatch.setattr(probe, "_psutil_loop_running", lambda: True)
    monkeypatch.setattr(probe, "_task_running", lambda name: False)
    monkeypatch.setattr(probe, "_powershell_loop_running", lambda: False)
    assert probe._loop_process_running() is True


def test_task_running_is_fallback_after_psutil_miss(monkeypatch):
    monkeypatch.setattr(probe, "_psutil_loop_running", lambda: False)
    monkeypatch.setattr(probe, "_task_running", lambda name: name == "WatchEngineLoop")
    assert probe._loop_process_running() is True


def test_false_when_process_and_task_are_down(monkeypatch):
    monkeypatch.setattr(probe, "_psutil_loop_running", lambda: False)
    monkeypatch.setattr(probe, "_task_running", lambda name: False)
    assert probe._loop_process_running() is False


def test_powershell_fallback_when_psutil_unavailable(monkeypatch):
    monkeypatch.setattr(probe, "_psutil_loop_running", lambda: None)
    monkeypatch.setattr(probe, "_powershell_loop_running", lambda: True)
    assert probe._loop_process_running() is True


def test_psutil_unavailable_and_legacy_miss_is_false(monkeypatch):
    monkeypatch.setattr(probe, "_psutil_loop_running", lambda: None)
    monkeypatch.setattr(probe, "_powershell_loop_running", lambda: False)
    assert probe._loop_process_running() is False
