# -*- coding: utf-8 -*-
"""devtask 帮助入口回归测试（2026-11-06）。

历史事故：``devtask.py new --help`` 曾被 cmd_new 当成 TID，创建
``--help-aborted-*`` 空单并污染任务板。帮助参数必须在命令分发前被拦截，
且不得触发 git 自愈/归属门/建单等任何写动作。
"""
import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
spec = importlib.util.spec_from_file_location("devtask_help_t", TOOLS / "devtask.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_subcommand_help_never_dispatches(monkeypatch, capsys, flag):
    """``new --help`` / ``new -h`` 只打印用法，不进入 cmd_new 等写路径。"""
    calls = []

    def boom(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("help 参数不得进入命令分发")

    monkeypatch.setattr(m, "_git_health_autofix", boom)
    monkeypatch.setattr(m, "_attribution_gate", boom)
    monkeypatch.setattr(m, "cmd_new", boom)
    monkeypatch.setattr(sys, "argv", ["devtask.py", "new", flag])

    assert m.main() == 0
    assert calls == []
    assert capsys.readouterr().out.strip()


def test_help_command_still_prints_usage(monkeypatch, capsys):
    """``devtask.py help`` 保持原有只读行为。"""
    monkeypatch.setattr(sys, "argv", ["devtask.py", "help"])
    assert m.main() == 0
    out = capsys.readouterr().out
    assert out.strip()
    assert "不要用 `--help` 建单" in out
    assert "--help" in out and "TID" in out


@pytest.mark.parametrize("tid", [
    None,
    "",
    " ",
    "--help",
    "-h",
    " -leading-space",
    "bad/tid",
    r"bad\tid",
    "bad tid",
    "bad:tid",
    "bad*tid",
    "..",
    ".hidden",
    "bad..tid",
    "bad.",
    "bad\nid",
    "x" * 65,
])
def test_invalid_tid_rejected_before_any_side_effect(monkeypatch, tid):
    """非法 TID 必须在 worktree cap、读账本、建 worktree 之前直接拒收。"""
    calls = []

    def boom(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("非法 TID 不得进入任何建单副作用")

    monkeypatch.setattr(m, "_enforce_worktree_cap", boom)
    monkeypatch.setattr(m, "_tasks", boom)

    with pytest.raises(SystemExit) as exc:
        m.cmd_new(tid)
    assert "非法 TID" in str(exc.value)
    assert calls == []


def test_missing_tid_fails_cleanly(monkeypatch):
    """``devtask.py new`` 不带 TID 时给出明确错误，而不是 TypeError traceback。"""
    def boom(*args, **kwargs):
        raise AssertionError("缺 TID 不得进入建单副作用")

    monkeypatch.setattr(m, "_enforce_worktree_cap", boom)
    with pytest.raises(SystemExit) as exc:
        m.cmd_new()
    assert "非法 TID" in str(exc.value)


@pytest.mark.parametrize("tid", [
    "K-ORG-015a",
    "WO-DEV-1",
    "CM-20261023-0900-田丰",
    "a_b.c-d",
])
def test_valid_tid_shape_is_preserved(tid):
    """合法 TID（含中文/下划线/点/短横线）保持原样。"""
    assert m._validate_tid(tid) == tid
