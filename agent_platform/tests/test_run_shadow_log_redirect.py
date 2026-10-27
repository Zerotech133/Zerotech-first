# -*- coding: utf-8 -*-
"""run_shadow 日志重定向护栏测试（K-OPS-20261019-SHADOWFIX）。

背景：生产包装器 shadow_automation.ps1 run 分支外层已
`cmd /c "... > last_run.log 2>&1"`，cmd 重定向句柄共享模式拒绝二次写打开
→ run_shadow.main() 再 open 同一文件必 PermissionError（10-19 17:10 首跑
367 行即崩，连锁 gate3 FAIL 6/7）。修复后：被占用时沿用既有 stdout/stderr
（本就已被外层重定向到同一文件），不再自抢。本组锁死契约：

1. 文件被占（PermissionError）→ 返回 None，不抛、不换流；
2. 文件空闲 → 正常以追加模式打开，可写；
3. Windows 真实共享冲突复现（CreateFileW dwShareMode=0 持有句柄，模拟
   cmd 重定向）→ 同样返回 None。

纪律：全部 tmp 隔离（本测试组不落任何 agent_platform/data/ 文件）。
"""

import ctypes
import os
import sys
from pathlib import Path

import pytest

PLATFORM = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLATFORM))
sys.path.insert(0, str(PLATFORM / "engine"))

import run_shadow  # noqa: E402


@pytest.fixture
def ledger_root(monkeypatch, tmp_path):
    """把 LEDGER 指到 tmp 隔离根（辅助函数从 LEDGER.parent 反推日志路径）。"""
    ledger = tmp_path / "shadow" / "shadow_ledger.jsonl"
    monkeypatch.setattr(run_shadow, "LEDGER", ledger)
    return ledger


def test_occupied_falls_back_returns_none(monkeypatch, ledger_root):
    """契约 1：open 抛 PermissionError → 返回 None，不上抛（fail-open）。"""
    real_open = open

    def _occupied(path, *a, **kw):
        if Path(path).name == "last_run.log":
            raise PermissionError(13, "Permission denied", str(path))
        return real_open(path, *a, **kw)

    monkeypatch.setattr("builtins.open", _occupied)
    assert run_shadow._redirect_run_log() is None


def test_free_file_opens_append_writable(ledger_root):
    """契约 2：文件空闲 → 正常打开，追加写不截断既有内容。"""
    ledger_root.parent.mkdir(parents=True, exist_ok=True)
    (ledger_root.parent / "last_run.log").write_text("prior\n", encoding="utf-8")
    f = run_shadow._redirect_run_log()
    assert f is not None
    f.write("new\n")
    f.close()
    body = (ledger_root.parent / "last_run.log").read_text(encoding="utf-8")
    assert body == "prior\nnew\n"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 共享冲突语义")
def test_windows_share_violation_simulates_cmd_redirect(ledger_root):
    """契约 3：真实复现——以 dwShareMode=0 独占持有文件（等价 cmd 重定向
    拒绝写共享），_redirect_run_log 应返回 None 而非崩。"""
    ledger_root.parent.mkdir(parents=True, exist_ok=True)
    log_path = ledger_root.parent / "last_run.log"
    log_path.write_bytes(b"")
    GENERIC_WRITE = 0x40000000
    OPEN_ALWAYS = 4
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    h = ctypes.windll.kernel32.CreateFileW(
        str(log_path), GENERIC_WRITE, 0, None, OPEN_ALWAYS, 0, None
    )
    assert h != INVALID_HANDLE_VALUE, "测试前置：独占句柄获取失败"
    try:
        assert run_shadow._redirect_run_log() is None
    finally:
        ctypes.windll.kernel32.CloseHandle(h)
    # 释放后可恢复打开（不残留占用状态）
    assert run_shadow._redirect_run_log() is not None
    os.remove(log_path)
