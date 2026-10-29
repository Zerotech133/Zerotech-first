# -*- coding: utf-8 -*-
"""task_board_audit 单测：分类纯函数 + 不可读 tasks 兜底。"""
import importlib.util
import json
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "task_board_audit.py"


def _load():
    spec = importlib.util.spec_from_file_location("task_board_audit_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_classify_all():
    m = _load()
    assert m.classify(status="done", branch_exists=False, merged=False,
                      ahead=0, worktree_exists=False, dirty=False) == "closed"
    assert m.classify(status="doing", branch_exists=False, merged=False,
                      ahead=0, worktree_exists=False, dirty=False) == "branch_missing"
    assert m.classify(status="doing", branch_exists=True, merged=True,
                      ahead=0, worktree_exists=False, dirty=False) == "merged_but_open"
    assert m.classify(status="review", branch_exists=True, merged=False,
                      ahead=2, worktree_exists=True, dirty=False) == "ahead_unmerged"
    assert m.classify(status="doing", branch_exists=True, merged=False,
                      ahead=0, worktree_exists=False, dirty=False) == "worktree_missing"
    assert m.classify(status="doing", branch_exists=True, merged=False,
                      ahead=0, worktree_exists=True, dirty=True) == "worktree_dirty"
    assert m.classify(status="doing", branch_exists=True, merged=False,
                      ahead=0, worktree_exists=True, dirty=False) == "clean"


def test_audit_missing_tasks(tmp_path):
    m = _load()
    rep = m.audit(repo=tmp_path, tasks_path=tmp_path / "nope.json")
    assert rep["ok"] is False and "unreadable" in rep["error"] or "不可读" in rep["error"]


def test_audit_empty_tasks(tmp_path):
    m = _load()
    p = tmp_path / "tasks.json"
    p.write_text(json.dumps({"WO-A": {"status": "done"}}), encoding="utf-8")
    rep = m.audit(repo=tmp_path, tasks_path=p)
    assert rep["ok"] and rep["open_tasks"] == 0 and rep["summary"] == {}
