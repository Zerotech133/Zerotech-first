# -*- coding: utf-8 -*-
"""LDE exec 进程隔离层（Windows Job Object）。

定位：**资源/进程树隔离**，不是文件系统沙箱。
- 命令启动后立即加入 Job Object；
- 超时用 TerminateJobObject 整树终止，避免子进程残留；
- IDE_EXEC_ISOLATION=off 可回退普通 subprocess；
- 非 Windows 或 Job API 不可用时 fail-soft 回退，不影响命令本身。

真正的文件系统/低权限隔离仍需独立容器/低权限账户，见实战准入报告。
"""
from __future__ import annotations

import ctypes
import os
import subprocess
from types import SimpleNamespace

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9


class _BasicLimit(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimit),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def enabled() -> bool:
    return os.name == "nt" and os.environ.get(
        "IDE_EXEC_ISOLATION", "job"
    ).strip().lower() not in ("0", "off", "false", "no")


def _close(handle) -> None:
    try:
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        pass


def _plain(argv, cwd, timeout, env):
    p = subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=max(1, int(timeout)),
        env=env,
    )
    return SimpleNamespace(returncode=p.returncode, stdout=p.stdout or "",
                           stderr=p.stderr or "", isolation="subprocess")


def run(argv, cwd: str, timeout: int, env: dict):
    if not enabled():
        return _plain(argv, cwd, timeout, env)
    k32 = ctypes.windll.kernel32
    k32.CreateJobObjectW.restype = ctypes.c_void_p
    k32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k32.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    job = k32.CreateJobObjectW(None, None)
    if not job:
        return _plain(argv, cwd, timeout, env)
    # KILL_ON_JOB_CLOSE：句柄关闭/进程异常时整树清理；ActiveProcessLimit 32 防 fork 炸弹。
    info = _ExtendedLimit()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    info.BasicLimitInformation.ActiveProcessLimit = 32
    try:
        k32.SetInformationJobObject(
            ctypes.c_void_p(job), JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info), ctypes.sizeof(info),
        )
    except Exception:
        pass
    proc = subprocess.Popen(
        argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", env=env,
    )
    try:
        assigned = bool(k32.AssignProcessToJobObject(
            ctypes.c_void_p(job), ctypes.c_void_p(int(proc._handle))
        ))
    except Exception:
        assigned = False
    try:
        try:
            out, err = proc.communicate(timeout=max(1, int(timeout)))
        except subprocess.TimeoutExpired:
            try:
                k32.TerminateJobObject(ctypes.c_void_p(job), 1)
            except Exception:
                proc.kill()
            try:
                out, err = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                out, err = "", ""
            raise subprocess.TimeoutExpired(argv, timeout, output=out, stderr=err)
        return SimpleNamespace(
            returncode=proc.returncode, stdout=out or "", stderr=err or "",
            isolation="job" if assigned else "job-assign-failed",
        )
    finally:
        _close(job)
