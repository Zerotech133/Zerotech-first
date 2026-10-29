# -*- coding: utf-8 -*-
"""merge 串行锁单测（tmp 锁路径，不碰真实 C:\\Temp）。"""
import importlib.util
import json
import os
import time
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "merge_lock.py"


def _load(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("merge_lock_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    lock = tmp_path / "aios_merge.lock"
    monkeypatch.setattr(mod, "LOCK", lock)
    monkeypatch.delenv(mod.ENV_TOKEN, raising=False)
    return mod, lock


def test_acquire_release_roundtrip(tmp_path, monkeypatch):
    m, lock = _load(tmp_path, monkeypatch)
    tok = m.acquire("t1", timeout=0)
    assert tok and lock.exists()
    assert m.held()["token"] == tok
    assert m.release(tok) is True
    assert not lock.exists()


def test_second_acquire_times_out(tmp_path, monkeypatch):
    m, lock = _load(tmp_path, monkeypatch)
    assert m.acquire("t1", timeout=0)
    assert m.acquire("t2", timeout=0) is None


def test_stale_lock_reclaimed(tmp_path, monkeypatch):
    m, lock = _load(tmp_path, monkeypatch)
    lock.write_text(json.dumps({"pid": 1, "token": "old"}), encoding="utf-8")
    old = time.time() - 3600
    os.utime(lock, (old, old))
    tok = m.acquire("t2", timeout=0, stale_sec=60)
    assert tok and m.held()["token"] == tok


def test_release_wrong_token_noop(tmp_path, monkeypatch):
    m, lock = _load(tmp_path, monkeypatch)
    tok = m.acquire("t1", timeout=0)
    assert m.release("not-mine") is False
    assert lock.exists()
    assert m.release(tok) is True


def test_context_reentrant_via_env(tmp_path, monkeypatch):
    m, lock = _load(tmp_path, monkeypatch)
    tok = m.acquire("outer", timeout=0)
    monkeypatch.setenv(m.ENV_TOKEN, tok)
    with m.merge_lock(timeout=0) as inner:
        assert inner == tok
    assert lock.exists()  # 内层不释放父锁
    assert m.release(tok) is True
