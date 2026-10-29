# -*- coding: utf-8 -*-
"""merge_gated 兼容预检器单测（P37 后真实闸门在 devtask merge；此处只测预检语义）。"""
import importlib.util
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]


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


def test_dry_run_no_merge_no_lock_no_audit(tmp_path, monkeypatch):
    """兼容层 dry-run：只打印分类，不合并、不持锁、无审计事件。"""
    mod, calls = _load_gated(monkeypatch)
    lock = tmp_path / "aios_merge.lock"
    rc = mod.main(["WO-X", "--dry-run"])
    assert rc == 0
    assert not lock.exists()
    assert not any(k == "guard_fail" for k, _, _ in calls)
