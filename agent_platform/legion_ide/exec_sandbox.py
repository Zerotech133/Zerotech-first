# -*- coding: utf-8 -*-
"""LDE exec 进程隔离层（Windows Job Object）· fail-closed。

定位：**资源/进程树隔离**，不是文件系统沙箱。
- 命令启动后立即加入 Job Object；
- 超时用 TerminateJobObject 整树终止，避免子进程残留；
- **fail-closed（2026-10-31 K-ORG-20261031-SANDBOXFAILCLOSED）**：创建 Job /
  SetInformationJobObject / AssignProcessToJobObject 任一失败 → 终止并回收进程，
  抛 ``IsolationError``（OSError 子类）；上层 exec_runner 已把 OSError 映射为
  ``exec_failed``，绝不把「未隔离执行」静默当成功。
- 唯一回退：``IDE_EXEC_ISOLATION=off|0|false|no`` 显式关闭（用户知情选择普通
  subprocess，非失败路径）；非 Windows 在未显式关闭时同样 fail-closed。

真正的文件系统/低权限隔离仍需独立容器/低权限账户，见实战准入报告。
"""
from __future__ import annotations

import logging
import ctypes
import os
import subprocess
from types import SimpleNamespace

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
# H6 OS 级兜底：单进程/整 Job 内存上限 + 活跃进程数上限（防 fork 炸弹/内存炸弹）。
PROCESS_MEMORY_LIMIT = 512 * 1024 * 1024
JOB_MEMORY_LIMIT = 768 * 1024 * 1024
ACTIVE_PROCESS_LIMIT = 32


class IsolationError(OSError):
    """进程隔离建立失败（fail-closed）：命令未执行或已终止；上层按执行失败处理。"""


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


def _requested() -> bool:
    """用户是否要求隔离（默认要求；仅显式 off 等值才关闭）。"""
    return os.environ.get(
        "IDE_EXEC_ISOLATION", "job"
    ).strip().lower() not in ("0", "off", "false", "no")


def enabled() -> bool:
    """当前平台/配置下是否启用 Job Object 隔离。"""
    return os.name == "nt" and _requested()


def _close(handle) -> None:
    try:
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        logging.getLogger(__name__).exception("swallowed exception at line 89")
        pass


def attach_job(proc):
    """把已启动的 Popen 进程挂到新 Job Object；失败 fail-closed（调用方必须杀进程）。

    H6（2026-11-01）：给 codex 插件子进程提供 OS 级兜底——
    KILL_ON_JOB_CLOSE + 单进程/整 Job 内存上限 + ActiveProcessLimit。
    这仍不是 AppContainer/网络/文件系统级隔离；AST 仍只是纵深防御。
    返回 job handle；未启用隔离（IDE_EXEC_ISOLATION=off）返回 None。
    """
    if not _requested():
        return None
    if os.name != "nt" or proc is None:
        raise IsolationError("job_isolation_requires_windows")
    k32 = ctypes.windll.kernel32
    k32.CreateJobObjectW.restype = ctypes.c_void_p
    k32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    job = k32.CreateJobObjectW(None, None)
    if not job:
        raise IsolationError("create_job_object_failed")
    try:
        info = _ExtendedLimit()
        info.BasicLimitInformation.LimitFlags = (
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            | JOB_OBJECT_LIMIT_PROCESS_MEMORY
            | JOB_OBJECT_LIMIT_JOB_MEMORY
        )
        info.BasicLimitInformation.ActiveProcessLimit = ACTIVE_PROCESS_LIMIT
        info.ProcessMemoryLimit = PROCESS_MEMORY_LIMIT
        info.JobMemoryLimit = JOB_MEMORY_LIMIT
        ok = bool(k32.SetInformationJobObject(
            ctypes.c_void_p(job), JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info), ctypes.sizeof(info),
        ))
        if not ok:
            raise IsolationError("set_job_information_failed")
        assigned = bool(k32.AssignProcessToJobObject(
            ctypes.c_void_p(job), ctypes.c_void_p(int(proc._handle))
        ))
        if not assigned:
            raise IsolationError("assign_process_to_job_failed")
        return job
    except Exception:
        _close(job)
        raise


def close_job(job) -> None:
    """关闭 Job（KILL_ON_JOB_CLOSE 会清理残留子进程）；None/off 安全 no-op。"""
    if job:
        _close(job)


def _plain(argv, cwd, timeout, env):
    """显式关闭隔离时的普通 subprocess 回退（非失败路径）。"""
    p = subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=max(1, int(timeout)),
        env=env,
    )
    return SimpleNamespace(returncode=p.returncode, stdout=p.stdout or "",
                           stderr=p.stderr or "", isolation="subprocess")


def _terminate_and_reap(k32, job, proc) -> None:
    """fail-closed 清理：整树终止 + kill + 回收，尽量不泄露进程。"""
    if job:
        try:
            k32.TerminateJobObject(ctypes.c_void_p(job), 1)
        except Exception:
            logging.getLogger(__name__).exception("swallowed exception at line 160")
            pass
    try:
        proc.kill()
    except Exception:
        logging.getLogger(__name__).exception("swallowed exception at line 164")
        pass
    try:
        proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            logging.getLogger(__name__).exception("swallowed exception at line 171")
            pass
        try:
            proc.communicate(timeout=5)
        except Exception:
            logging.getLogger(__name__).exception("swallowed exception at line 175")
            pass
    except Exception:
        logging.getLogger(__name__).exception("swallowed exception at line 177")
        pass


def run(argv, cwd: str, timeout: int, env: dict):
    # 唯一允许的非隔离路径：用户显式关闭（IDE_EXEC_ISOLATION=off 等）。
    if not _requested():
        return _plain(argv, cwd, timeout, env)
    if os.name != "nt":
        raise IsolationError("job_isolation_requires_windows")
    k32 = ctypes.windll.kernel32
    k32.CreateJobObjectW.restype = ctypes.c_void_p
    k32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k32.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]

    job = k32.CreateJobObjectW(None, None)
    if not job:
        # fail-closed：拿不到 Job 就不执行命令
        raise IsolationError("create_job_object_failed")
    try:
        # KILL_ON_JOB_CLOSE：句柄关闭/进程异常时整树清理；ActiveProcessLimit 32 防 fork 炸弹。
        info = _ExtendedLimit()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        info.BasicLimitInformation.ActiveProcessLimit = 32
        try:
            set_ok = bool(k32.SetInformationJobObject(
                ctypes.c_void_p(job), JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(info), ctypes.sizeof(info),
            ))
        except Exception as exc:
            raise IsolationError(
                "set_job_information_failed: %s: %s" % (type(exc).__name__, exc)
            ) from exc
        if not set_ok:
            raise IsolationError("set_job_information_failed: SetInformationJobObject=false")

        proc = subprocess.Popen(
            argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", env=env,
        )
        assign_error = None
        assigned = False
        try:
            assigned = bool(k32.AssignProcessToJobObject(
                ctypes.c_void_p(job), ctypes.c_void_p(int(proc._handle))
            ))
        except Exception as exc:
            assign_error = exc
        if assign_error is not None or not assigned:
            # fail-closed：未隔离的进程不可继续跑 → 立即终止/回收，再抛错
            _terminate_and_reap(k32, job, proc)
            detail = (
                "%s: %s" % (type(assign_error).__name__, assign_error)
                if assign_error is not None
                else "AssignProcessToJobObject=false"
            )
            raise IsolationError("assign_process_to_job_failed: %s" % detail)

        try:
            out, err = proc.communicate(timeout=max(1, int(timeout)))
        except subprocess.TimeoutExpired:
            _terminate_and_reap(k32, job, proc)
            raise
        return SimpleNamespace(
            returncode=proc.returncode, stdout=out or "", stderr=err or "",
            isolation="job",
        )
    finally:
        _close(job)
