# -*- coding: utf-8 -*-
"""通用跨进程文件锁（默认安全：同刻只允许一个持有者）。

用途：数据导出等长任务串行化，避免并发写同一目标（如 write_active 的
`_active.json.tmp` → `_active.json` 原子替换在 WinError 32 下被跳过）。

设计同 tools/merge_lock.py：O_CREAT|O_EXCL 原子创建、JSON 元数据、stale 回收、
token 重入（env 变量默认 AIOS_FILE_LOCK_TOKEN，可按锁区分）。
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

TMP = Path(os.environ.get("TEMP", r"C:\Temp"))
ENV_TOKEN = "AIOS_FILE_LOCK_TOKEN"
STALE_SEC = 30 * 60


def _pid_alive(pid) -> bool:
    """持有者进程是否存活（2026-10-30 P82：死 PID 锁立即回收，不再等 30min）。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        import psutil  # noqa: PLC0415
    except ImportError:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    try:
        return psutil.pid_exists(pid)
    except Exception:  # noqa: BLE001
        return True


def _read(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def acquire(lock: Path, cmd: str = "", timeout: float = 0,
            stale_sec: float = STALE_SEC, env_token: str = ENV_TOKEN):
    """获取锁；返回 token 或 None（超时）。timeout=0 表示只试一次。"""
    lock = Path(lock)
    lock.parent.mkdir(parents=True, exist_ok=True)
    parent = os.environ.get(env_token)
    if parent and _read(lock).get("token") == parent:
        return parent
    deadline = time.time() + max(0.0, timeout)
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            info = _read(lock)
            owner = info.get("pid")
            if owner is not None and not _pid_alive(owner):
                stale = lock.with_name(lock.name + ".stale.%d" % int(time.time()))
                try:
                    os.replace(lock, stale)
                    print("[file-lock] 死 PID 锁已回收: pid=%s -> %s" % (owner, stale))
                    continue
                except OSError:
                    pass
            try:
                age = time.time() - lock.stat().st_mtime
            except OSError:
                age = 0
            if age > stale_sec:
                stale = lock.with_name(lock.name + ".stale.%d" % int(time.time()))
                try:
                    os.replace(lock, stale)
                    print("[file-lock] stale lock 已归档: %s" % stale)
                    continue
                except OSError:
                    pass
            if time.time() >= deadline:
                return None
            time.sleep(1.0)
            continue
        token = uuid.uuid4().hex
        payload = {"pid": os.getpid(), "token": token,
                   "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "cmd": cmd[:200]}
        try:
            os.write(fd, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        finally:
            os.close(fd)
        return token


def release(lock: Path, token: str, env_token: str = ENV_TOKEN) -> bool:
    lock = Path(lock)
    if not token:
        return False
    if _read(lock).get("token") != token:
        return False
    try:
        lock.unlink()
        return True
    except OSError:
        return False


def held(lock: Path) -> dict | None:
    lock = Path(lock)
    info = _read(lock)
    if not info:
        return None
    try:
        info["age_sec"] = round(time.time() - lock.stat().st_mtime, 1)
    except OSError:
        info["age_sec"] = None
    return info
