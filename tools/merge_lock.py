# -*- coding: utf-8 -*-
"""跨进程 merge 串行锁（默认安全：同刻只允许一个 merge_gated/devtask merge）。

设计：
- 锁文件 C:\\Temp\\aios_merge.lock（O_CREAT|O_EXCL 原子创建）；
- 内容 JSON：{pid, token, ts, cmd}；
- 持有超时（默认 15min）判定 stale → 归档为 aios_merge.stale.<ts> 后接管；
- 子进程通过环境变量 AIOS_MERGE_LOCK_TOKEN 识别父锁，不重复获取；
- 释放仅当 token 匹配（防误删他人锁）。
"""
from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

TMP = Path(os.environ.get("TEMP", r"C:\Temp"))
LOCK = TMP / "aios_merge.lock"
ENV_TOKEN = "AIOS_MERGE_LOCK_TOKEN"
STALE_SEC = 15 * 60


def _read(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _pid_alive(pid: int) -> bool:
    """持锁进程是否还活着（Windows：OpenProcess + GetExitCodeProcess 探测）。"""
    if not pid or pid <= 0:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, int(pid))
        if not h:
            return False
        try:
            code = wintypes.DWORD()
            if k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return True
        finally:
            k32.CloseHandle(h)
    except Exception:  # noqa: BLE001
        return True


def acquire(cmd: str = "", timeout: float = 0, stale_sec: float = STALE_SEC):
    """获取锁；返回 token 或 None（超时）。timeout=0 表示只试一次。"""
    deadline = time.time() + max(0.0, timeout)
    while True:
        try:
            fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - LOCK.stat().st_mtime
            except OSError:
                age = 0
            info = _read(LOCK)
            try:
                holder_pid = int((info or {}).get("pid") or 0)
            except (TypeError, ValueError):
                holder_pid = 0
            holder_dead = bool(holder_pid) and not _pid_alive(holder_pid)
            if holder_dead:
                print("[merge-lock] 持锁进程 %d 已退出，判 stale 接管" % holder_pid)
            if holder_dead or age > stale_sec:
                stale = LOCK.with_name("aios_merge.stale.%d" % int(time.time()))
                try:
                    os.replace(LOCK, stale)
                    print("[merge-lock] stale lock 已归档: %s" % stale)
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


def release(token: str) -> bool:
    if not token:
        return False
    info = _read(LOCK)
    if info.get("token") != token:
        return False
    try:
        LOCK.unlink()
        return True
    except OSError:
        return False


def held() -> dict | None:
    info = _read(LOCK)
    if not info:
        return None
    try:
        info["age_sec"] = round(time.time() - LOCK.stat().st_mtime, 1)
    except OSError:
        info["age_sec"] = None
    return info


@contextmanager
def merge_lock(cmd: str = "", timeout: float = 0):
    """重入友好：父进程已持锁（env token）时直接复用。"""
    parent = os.environ.get(ENV_TOKEN)
    if parent:
        yield parent
        return
    token = acquire(cmd=cmd, timeout=timeout)
    if token is None:
        raise TimeoutError("merge lock busy: %s" % json.dumps(held(), ensure_ascii=False))
    os.environ[ENV_TOKEN] = token
    try:
        yield token
    finally:
        if os.environ.get(ENV_TOKEN) == token:
            os.environ.pop(ENV_TOKEN, None)
        release(token)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="merge 串行锁")
    ap.add_argument("action", choices=("status", "acquire", "release"))
    ap.add_argument("--timeout", type=float, default=0)
    args = ap.parse_args(argv)
    if args.action == "status":
        print(json.dumps(held(), ensure_ascii=False))
        return 0 if held() is None else 1
    if args.action == "acquire":
        tok = acquire(cmd="cli", timeout=args.timeout)
        print(tok or "")
        return 0 if tok else 1
    tok = os.environ.get(ENV_TOKEN, "")
    print("released" if release(tok) else "not-holder")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
