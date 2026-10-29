# -*- coding: utf-8 -*-
"""devtask 合并锁单测（2026-10-29 · 防并发 HEAD 竞争）。"""
import importlib.util
import json
import os
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
spec = importlib.util.spec_from_file_location("devtask_lock_t", TOOLS / "devtask.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def _write_lock(path, pid, age_s=0.0, tid="other"):
    path.write_text(json.dumps({"pid": pid, "host": "h", "tid": tid,
                                "start": time.time() - age_s}), encoding="utf-8")


def test_acquire_release_and_exclusive(tmp_path):
    lock = tmp_path / "devtask_merge.lock"
    p = m._acquire_merge_lock("T1", timeout_s=0.2, path=str(lock))
    assert p == str(lock) and lock.exists()
    meta = json.loads(lock.read_text(encoding="utf-8"))
    assert meta["pid"] == os.getpid() and meta["tid"] == "T1"
    # 第二持有者超时失败（不抢锁）
    assert m._acquire_merge_lock("T2", timeout_s=0.05, path=str(lock)) is None
    assert m._release_merge_lock(p) is True
    assert not lock.exists()
    # 释放后可再获取
    p2 = m._acquire_merge_lock("T3", timeout_s=0.2, path=str(lock))
    assert p2 is not None
    m._release_merge_lock(p2)


def test_stale_lock_reclaimed(tmp_path):
    lock = tmp_path / "devtask_merge.lock"
    _write_lock(lock, pid=999999, age_s=99999)
    p = m._acquire_merge_lock("T1", timeout_s=2.0, path=str(lock))
    assert p is not None and lock.exists()
    meta = json.loads(lock.read_text(encoding="utf-8"))
    assert meta["pid"] == os.getpid()
    m._release_merge_lock(p)


def test_release_does_not_remove_other_pid(tmp_path):
    lock = tmp_path / "devtask_merge.lock"
    _write_lock(lock, pid=123456, age_s=0)
    assert m._release_merge_lock(str(lock)) is False
    assert lock.exists()
    lock.unlink()


def test_merge_lock_path_in_git_dir():
    p = m._merge_lock_path()
    assert p.endswith("devtask_merge.lock")
