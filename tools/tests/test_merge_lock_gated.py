# -*- coding: utf-8 -*-
"""merge_gated 锁接线单测（不跑真实测试门，干跑验证获取/释放/忙碌拒绝）。"""
import importlib.util
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
LOCK_TOOL = TOOLS / "merge_lock.py"


def _load_lock(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("merge_lock_g", LOCK_TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    lock = tmp_path / "aios_merge.lock"
    monkeypatch.setattr(mod, "LOCK", lock)
    monkeypatch.delenv(mod.ENV_TOKEN, raising=False)
    return mod, lock


def _load_gated(monkeypatch):
    sys.path.insert(0, str(TOOLS))
    spec = importlib.util.spec_from_file_location("merge_gated_test", TOOLS / "merge_gated.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "branch_changed_files", lambda tid: ["docs/x.md"])
    monkeypatch.setattr(mod, "is_docs_only", lambda files: True)
    monkeypatch.setattr(mod, "run", lambda cmd, timeout=3600: 0)
    calls = []
    monkeypatch.setattr(mod, "emit", lambda k, t, d: calls.append((k, t, d)))
    return mod, calls


def test_gated_acquires_and_releases(tmp_path, monkeypatch):
    m, lock = _load_lock(tmp_path, monkeypatch)
    mod, calls = _load_gated(monkeypatch)
    sys.modules["merge_lock"] = m
    rc = mod.main(["WO-X", "--dry-run"])
    assert rc == 0
    assert not lock.exists(), "锁应在 dry-run 后释放"
    assert not any(k == "guard_fail" for k, _, _ in calls)


def test_gated_lock_busy_rejects(tmp_path, monkeypatch):
    m, lock = _load_lock(tmp_path, monkeypatch)
    assert m.acquire("holder", timeout=0)
    mod, calls = _load_gated(monkeypatch)
    sys.modules["merge_lock"] = m
    monkeypatch.setenv("AIOS_MERGE_LOCK_TIMEOUT", "0")
    rc = mod.main(["WO-Y", "--dry-run"])
    assert rc == 2
    assert any(k == "guard_fail" for k, _, _ in calls)
    assert m.release(m.held()["token"]) is True
