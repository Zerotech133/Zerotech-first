# -*- coding: utf-8 -*-
"""devtask merge HEAD 竞态重试单测（fake git，不跑真实合并）。"""
import importlib.util
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
DEV = TOOLS / "devtask.py"


def _load():
    sys.path.insert(0, str(TOOLS))
    spec = importlib.util.spec_from_file_location("devtask_retry_test", DEV)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_classify_head_race():
    m = _load()
    assert m._is_merge_head_race(
        "fatal: update_ref failed for ref 'HEAD': cannot lock ref 'HEAD'")
    assert m._is_merge_head_race(
        "fatal: You have not concluded your merge (MERGE_HEAD exists)")
    assert not m._is_merge_head_race(
        "CONFLICT (content): Merge conflict in tools/x.py")


def test_retry_then_success():
    m = _load()
    calls = []

    def fake_git(*args):
        calls.append(args)
        if len(calls) == 1:
            raise SystemExit("fatal: update_ref failed for ref 'HEAD': cannot lock ref 'HEAD'")
        return ""

    events = []
    ok = m._merge_with_retry("WO-X", "task/WO-X", "demo", tries=3,
                             sleep_fn=lambda s: None, git_fn=fake_git,
                             emit_fn=lambda *a: events.append(a))
    assert ok is True
    assert len(calls) == 2
    assert events and events[0][0] == "guard_fail"


def test_real_conflict_not_retried():
    m = _load()
    calls = []

    def fake_git(*args):
        calls.append(args)
        raise SystemExit("CONFLICT (content): Merge conflict in tools/x.py")

    try:
        m._merge_with_retry("WO-Y", "task/WO-Y", "demo", tries=3,
                            sleep_fn=lambda s: None, git_fn=fake_git,
                            emit_fn=lambda *a: None)
        raise AssertionError("should raise")
    except SystemExit as e:
        assert "CONFLICT" in str(e)
    assert len(calls) == 1


def test_retry_exhausted_raises():
    m = _load()
    calls = []

    def fake_git(*args):
        calls.append(args)
        raise SystemExit("fatal: cannot lock ref 'HEAD'")

    try:
        m._merge_with_retry("WO-Z", "task/WO-Z", "demo", tries=2,
                            sleep_fn=lambda s: None, git_fn=fake_git,
                            emit_fn=lambda *a: None)
        raise AssertionError("should raise")
    except SystemExit as e:
        assert "cannot lock ref" in str(e)
    assert len(calls) == 2
