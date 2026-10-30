# -*- coding: utf-8 -*-
"""BATCH2 回归：ollama_lock 所有权校验。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import importlib.util

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("ollama_lock", REPO / "ollama_lock.py")
ol = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ol)


def _lock(tmp_path, pid, purpose="test"):
    p = tmp_path / "ollama_batch.lock"
    p.write_text(json.dumps({"pid": pid, "purpose": purpose, "ts": 0}), encoding="utf-8")
    return p


def test_heartbeat_true_when_own(tmp_path, monkeypatch):
    p = _lock(tmp_path, os.getpid())
    monkeypatch.setattr(ol, "LOCK_PATH", str(p))
    assert ol.heartbeat() is True


def test_heartbeat_false_when_foreign(tmp_path, monkeypatch):
    p = _lock(tmp_path, 12345)
    before = p.stat().st_mtime
    monkeypatch.setattr(ol, "LOCK_PATH", str(p))
    assert ol.heartbeat() is False
    assert p.stat().st_mtime == before


def test_release_does_not_delete_foreign(tmp_path, monkeypatch):
    p = _lock(tmp_path, 12345)
    monkeypatch.setattr(ol, "LOCK_PATH", str(p))
    ol.release()
    assert p.exists()


def test_release_does_not_delete_corrupt(tmp_path, monkeypatch):
    p = tmp_path / "ollama_batch.lock"
    p.write_text("not-json", encoding="utf-8")
    monkeypatch.setattr(ol, "LOCK_PATH", str(p))
    ol.release()
    assert p.exists()


def test_tick_reacquires_when_ownership_lost(tmp_path, monkeypatch):
    # 锁被别人（且进程已死）占用：tick 应重新抢回，而不是假装仍持有
    p = _lock(tmp_path, 999999)
    monkeypatch.setattr(ol, "LOCK_PATH", str(p))
    monkeypatch.setattr(ol, "LEASE_S", 600)
    assert ol.tick("test", lease_s=600) is True
    assert json.loads(p.read_text(encoding="utf-8"))["pid"] == os.getpid()
