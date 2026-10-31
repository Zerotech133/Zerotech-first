# -*- coding: utf-8 -*-
"""K-ORG-20261031-SANDBOXFAILCLOSED：exec_sandbox Job Object 失败 fail-closed 单测。

不触发真实内核崩溃：用 fake kernel32 + fake Popen 模拟 CreateJob / SetInformation /
AssignProcessToJobObject 失败，断言：
- 任一 Job 建立步骤失败 → 抛 IsolationError(OSError)；
- Assign 失败 → 先 kill/terminate/reap 进程再抛错（不泄露进程）；
- finally 关闭 Job 句柄；成功路径仍返回 returncode/isolation=job；
- 超时仍整树终止并抛 TimeoutExpired（回归）；
- IDE_EXEC_ISOLATION=off 显式回退普通 subprocess 语义（非失败）；
- exec_runner 的 OSError 分支把 IsolationError 映射为 exec_failed（不再当成功）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PLATFORM = Path(__file__).resolve().parents[1]
if str(PLATFORM) not in sys.path:
    sys.path.insert(0, str(PLATFORM))

from legion_ide import exec_sandbox as S  # noqa: E402
from legion_ide import exec_runner as X  # noqa: E402

WIN = (sys.platform == "win32")


# ── fakes ──────────────────────────────────────────────────────────────────

class _FakeProc:
    def __init__(self, rc=0, out="ok", err="", communicate_effects=None):
        self.returncode = rc
        self._out, self._err = out, err
        self._handle = 4242
        self.killed = False
        self.communicate_calls = 0
        self._effects = list(communicate_effects or [])

    def communicate(self, timeout=None):
        self.communicate_calls += 1
        if self._effects:
            eff = self._effects.pop(0)
            if isinstance(eff, BaseException):
                raise eff
            return eff
        return (self._out, self._err)

    def kill(self):
        self.killed = True
        self.returncode = -9


def _make_k32(*, create=111, setinfo=True, assign=True, setinfo_exc=None,
              assign_exc=None, terminate_exc=None):
    calls = {"create": 0, "setinfo": 0, "assign": 0, "terminate": 0, "close": 0}

    def create_fn(*a):
        calls["create"] += 1
        return create

    def setinfo_fn(*a):
        calls["setinfo"] += 1
        if setinfo_exc is not None:
            raise setinfo_exc
        return setinfo

    def assign_fn(*a):
        calls["assign"] += 1
        if assign_exc is not None:
            raise assign_exc
        return assign

    def terminate_fn(*a):
        calls["terminate"] += 1
        if terminate_exc is not None:
            raise terminate_exc
        return 1

    def close_fn(*a):
        calls["close"] += 1
        return 1

    k32 = SimpleNamespace(
        CreateJobObjectW=create_fn,
        SetInformationJobObject=setinfo_fn,
        AssignProcessToJobObject=assign_fn,
        TerminateJobObject=terminate_fn,
        CloseHandle=close_fn,
    )
    return k32, calls


@pytest.fixture()
def fake_win(monkeypatch):
    """安装 fake kernel32 + fake Popen；返回 (k32, calls, procs)。"""
    def install(*, create=111, setinfo=True, assign=True, setinfo_exc=None,
                assign_exc=None, terminate_exc=None, proc=None):
        k32, calls = _make_k32(create=create, setinfo=setinfo, assign=assign,
                               setinfo_exc=setinfo_exc, assign_exc=assign_exc,
                               terminate_exc=terminate_exc)
        procs = [proc or _FakeProc()]
        monkeypatch.setattr(S.ctypes, "windll", SimpleNamespace(kernel32=k32))
        monkeypatch.setattr(S.subprocess, "Popen", lambda *a, **k: procs[0])
        return k32, calls, procs

    return install


def _run(argv=None, timeout=5):
    return S.run(argv or ["cmd", "/c", "echo hi"], cwd=".", timeout=timeout, env={})


# ── 显式关闭：普通 subprocess（非失败路径） ────────────────────────────────

def test_explicit_off_falls_back_to_subprocess(monkeypatch):
    monkeypatch.setenv("IDE_EXEC_ISOLATION", "off")
    called = {}

    def fake_run(*a, **k):
        called["argv"] = a[0]
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(S.subprocess, "run", fake_run)
    res = _run()
    assert res.isolation == "subprocess"
    assert res.returncode == 0 and res.stdout == "ok"
    assert called["argv"] == ["cmd", "/c", "echo hi"]


def test_enabled_semantics(monkeypatch):
    monkeypatch.delenv("IDE_EXEC_ISOLATION", raising=False)
    assert S.enabled() is WIN
    monkeypatch.setenv("IDE_EXEC_ISOLATION", "off")
    assert S.enabled() is False
    monkeypatch.setenv("IDE_EXEC_ISOLATION", "job")
    assert S.enabled() is WIN


# ── fail-closed：Job 建立失败 ──────────────────────────────────────────────

@pytest.mark.skipif(not WIN, reason="Job Object 仅 Windows")
def test_create_job_failure_fails_closed(fake_win, monkeypatch):
    k32, calls, procs = fake_win(create=0)
    monkeypatch.setenv("IDE_EXEC_ISOLATION", "job")
    with pytest.raises(S.IsolationError, match="create_job_object_failed"):
        _run()
    assert procs[0].communicate_calls == 0


@pytest.mark.skipif(not WIN, reason="Job Object 仅 Windows")
def test_set_info_false_fails_closed(fake_win, monkeypatch):
    k32, calls, procs = fake_win(setinfo=False)
    monkeypatch.setenv("IDE_EXEC_ISOLATION", "job")
    with pytest.raises(S.IsolationError, match="set_job_information_failed"):
        _run()
    assert calls["close"] == 1              # finally 关闭 Job，不泄露句柄
    assert procs[0].communicate_calls == 0  # 命令未启动


@pytest.mark.skipif(not WIN, reason="Job Object 仅 Windows")
def test_set_info_exception_fails_closed(fake_win, monkeypatch):
    k32, calls, procs = fake_win(setinfo_exc=OSError("boom"))
    monkeypatch.setenv("IDE_EXEC_ISOLATION", "job")
    with pytest.raises(S.IsolationError, match="set_job_information_failed"):
        _run()
    assert calls["close"] == 1
    assert procs[0].communicate_calls == 0


# ── fail-closed：Assign 失败 → kill/reap 后抛错 ────────────────────────────

@pytest.mark.skipif(not WIN, reason="Job Object 仅 Windows")
def test_assign_false_kills_reaps_then_raises(fake_win, monkeypatch):
    proc = _FakeProc()
    k32, calls, procs = fake_win(assign=False, proc=proc)
    monkeypatch.setenv("IDE_EXEC_ISOLATION", "job")
    with pytest.raises(S.IsolationError, match="assign_process_to_job_failed"):
        _run()
    assert proc.killed is True                      # 进程被终止
    assert proc.communicate_calls >= 1              # 被回收（不泄露）
    assert calls["terminate"] >= 1                  # 先尝试整 Job 树终止
    assert calls["close"] == 1                      # Job 句柄关闭


@pytest.mark.skipif(not WIN, reason="Job Object 仅 Windows")
def test_assign_exception_kills_reaps_then_raises(fake_win, monkeypatch):
    proc = _FakeProc()
    k32, calls, procs = fake_win(assign_exc=OSError("denied"), proc=proc)
    monkeypatch.setenv("IDE_EXEC_ISOLATION", "job")
    with pytest.raises(S.IsolationError, match="assign_process_to_job_failed"):
        _run()
    assert proc.killed is True and proc.communicate_calls >= 1
    assert calls["close"] == 1


# ── 正常路径 / 超时回归 ────────────────────────────────────────────────────

@pytest.mark.skipif(not WIN, reason="Job Object 仅 Windows")
def test_success_still_returns_job_isolation(fake_win, monkeypatch):
    proc = _FakeProc(rc=3, out="OUT", err="ERR")
    k32, calls, procs = fake_win(proc=proc)
    monkeypatch.setenv("IDE_EXEC_ISOLATION", "job")
    res = _run()
    assert res.returncode == 3 and res.stdout == "OUT" and res.stderr == "ERR"
    assert res.isolation == "job"                    # 不再出现 job-assign-failed
    assert k32 is not None
    assert calls["assign"] == 1 and calls["close"] == 1


@pytest.mark.skipif(not WIN, reason="Job Object 仅 Windows")
def test_timeout_terminates_tree_and_raises(fake_win, monkeypatch):
    proc = _FakeProc(communicate_effects=[
        subprocess.TimeoutExpired(["cmd"], 1, output=b"", stderr=b""),
        ("", ""),
    ])
    k32, calls, procs = fake_win(proc=proc)
    monkeypatch.setenv("IDE_EXEC_ISOLATION", "job")
    with pytest.raises(subprocess.TimeoutExpired):
        _run(timeout=1)
    assert calls["terminate"] >= 1
    assert proc.killed is True
    assert calls["close"] == 1


# ── 非 Windows：未显式关闭 → fail-closed ───────────────────────────────────

def test_non_windows_fails_closed(monkeypatch):
    import os as _os
    monkeypatch.setenv("IDE_EXEC_ISOLATION", "job")
    monkeypatch.setattr(S, "os", SimpleNamespace(name="posix", environ=_os.environ))
    with pytest.raises(S.IsolationError, match="job_isolation_requires_windows"):
        _run()


# ── exec_runner 映射 ───────────────────────────────────────────────────────

def test_exec_runner_maps_isolation_error_to_exec_failed(tmp_path, monkeypatch):
    wt = tmp_path / "repo"
    wt.mkdir()

    def boom(*a, **k):
        raise S.IsolationError("assign_process_to_job_failed: AssignProcessToJobObject=false")

    monkeypatch.setattr(X.S, "run", boom)
    res = X.run(wt, "git status -s", timeout=5)
    assert res["ok"] is False
    assert res["error"] == "exec_failed"
    assert "IsolationError" in res["message"]
