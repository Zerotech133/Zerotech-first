# -*- coding: utf-8 -*-
"""file_lock 单测（tmp 锁路径）。"""
import importlib.util
import json
import os
import time
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "file_lock.py"


def _load(monkeypatch):
    spec = importlib.util.spec_from_file_location("file_lock_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.delenv(mod.ENV_TOKEN, raising=False)
    return mod


def test_acquire_release(tmp_path, monkeypatch):
    m = _load(monkeypatch)
    lock = tmp_path / ".export.lock"
    tok = m.acquire(lock, "t1")
    assert tok and lock.exists() and m.held(lock)["token"] == tok
    assert m.release(lock, tok) is True
    assert not lock.exists()


def test_second_acquire_times_out(tmp_path, monkeypatch):
    m = _load(monkeypatch)
    lock = tmp_path / ".export.lock"
    assert m.acquire(lock, "a", timeout=0)
    assert m.acquire(lock, "b", timeout=0) is None


def test_stale_reclaimed(tmp_path, monkeypatch):
    m = _load(monkeypatch)
    lock = tmp_path / ".export.lock"
    lock.write_text(json.dumps({"pid": 1, "token": "old"}), encoding="utf-8")
    old = time.time() - 7200
    os.utime(lock, (old, old))
    tok = m.acquire(lock, "new", timeout=0, stale_sec=60)
    assert tok and m.held(lock)["token"] == tok


def test_wrong_token_noop(tmp_path, monkeypatch):
    m = _load(monkeypatch)
    lock = tmp_path / ".export.lock"
    tok = m.acquire(lock, "a")
    assert m.release(lock, "other") is False
    assert m.release(lock, tok) is True

def test_dead_owner_reclaimed_immediately(tmp_path, monkeypatch):
    """2026-10-30 P82：持有者进程已死 → 锁立即回收（不等 stale_sec 30min）。"""
    m = _load(monkeypatch)
    lock = tmp_path / ".export.lock"
    lock.write_text(json.dumps({"pid": 99999999, "token": "dead"}), encoding="utf-8")
    fresh = time.time() - 5  # mtime 很新（<stale_sec），只有死 PID 判定能回收
    os.utime(lock, (fresh, fresh))
    tok = m.acquire(lock, "new", timeout=0, stale_sec=60)
    assert tok and m.held(lock)["token"] == tok
