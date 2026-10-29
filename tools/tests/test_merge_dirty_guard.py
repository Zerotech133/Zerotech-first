# -*- coding: utf-8 -*-
"""P49：devtask merge 脏文件快速失败守卫测试。

覆盖：
  1. 无重叠 → 放行；
  2. 重叠（分支改动 ∩ master 未提交）→ 拒绝并返回重叠路径；
  3. git status 不可得 → fail-open（不阻断合并）。
"""
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))

import devtask  # noqa: E402


def test_no_overlap_allows(monkeypatch):
    monkeypatch.setattr(devtask, "_git", lambda *a, **k: "docs/a.md\n")
    monkeypatch.setattr(devtask.subprocess, "run",
                        lambda *a, **k: type("R", (), {"returncode": 0, "stdout": " M other.py\n"})())
    ok, overlap = devtask._dirty_overlap_guard("T", "task/T")
    assert ok is True and overlap == []


def test_overlap_rejects(monkeypatch):
    monkeypatch.setattr(devtask, "_git", lambda *a, **k: "tools/devtask.py\ndocs/a.md\n")
    monkeypatch.setattr(devtask.subprocess, "run",
                        lambda *a, **k: type("R", (), {"returncode": 0,
                                                      "stdout": ' M tools/devtask.py\n?? docs/a.md\n'})())
    ok, overlap = devtask._dirty_overlap_guard("T", "task/T")
    assert ok is False
    assert overlap == ["docs/a.md", "tools/devtask.py"]


def test_status_unavailable_fail_open(monkeypatch):
    monkeypatch.setattr(devtask, "_git", lambda *a, **k: "tools/devtask.py\n")
    monkeypatch.setattr(devtask.subprocess, "run",
                        lambda *a, **k: type("R", (), {"returncode": 1, "stdout": ""})())
    ok, overlap = devtask._dirty_overlap_guard("T", "task/T")
    assert ok is True and overlap == []
