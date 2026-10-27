# -*- coding: utf-8 -*-
"""scan_secrets git_status_files 超时保护测试（审查 B4）。

锁定两点：
  1) subprocess.run 收到 timeout 参数（不再无限等 git）；
  2) TimeoutExpired 走既有「git status 执行失败」路径（返回 None → 退出码 2），
     标准错误留一行含超时秒数的诊断。
不改动任何既有测试断言。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import scan_secrets  # noqa: E402


def test_git_status_files_passes_timeout(monkeypatch):
    """正常路径：仍解析清单，且 subprocess.run 必须带 timeout。"""
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            cmd, 0, stdout=b"?? a.py\0 M b.py\0", stderr=b""
        )

    monkeypatch.setattr(scan_secrets.subprocess, "run", fake_run)
    assert scan_secrets.git_status_files("R") == ["a.py", "b.py"]
    assert seen["kwargs"].get("timeout") == scan_secrets.GIT_STATUS_TIMEOUT_S


def test_git_status_files_timeout_returns_none(monkeypatch, capsys):
    """超时路径：返回 None（既有失败语义），stderr 含秒数。"""

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    monkeypatch.setattr(scan_secrets.subprocess, "run", fake_run)
    assert scan_secrets.git_status_files("R") is None
    err = capsys.readouterr().err
    assert "超时" in err
    assert str(scan_secrets.GIT_STATUS_TIMEOUT_S) in err


def test_main_timeout_maps_to_exit_2(monkeypatch, capsys):
    """端到端：超时 → 「git status 执行失败」→ main 退出码 2（语义不变）。"""

    def fake_run(cmd, **kwargs):
        if "--show-toplevel" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="R\n", stderr="")
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    monkeypatch.setattr(scan_secrets.subprocess, "run", fake_run)
    assert scan_secrets.main([]) == 2
    err = capsys.readouterr().err
    assert "git status 执行失败" in err
    assert "超时" in err
