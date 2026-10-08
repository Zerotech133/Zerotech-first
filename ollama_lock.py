# -*- coding: utf-8 -*-
"""Ollama 批处理错峰锁（跨域共享：quant_backtest M3 / news_collector JEV 回填）。

背景（2026-10-08 事故）：M3 结构化与 JEV 回填同为本地 Ollama 重批处理，重叠运行
互相抢占推理槽，M3 单条从 ~20s 恶化到数小时（慢流滴答重置 requests 超时，详见
k266_m3_structure.ask_ollama 看门狗注释）。本锁让两个批处理互斥：抢不到就在
wait_s 内轮询，超时本轮让位，靠各自调度周期自愈追平。

用法：
    import ollama_lock
    if not ollama_lock.acquire("k266_m3", wait_s=1900):
        return                      # 本轮让位
    try:
        for item in items:
            ...                     # 每处理一条
            ollama_lock.heartbeat()
    finally:
        ollama_lock.release()

语义：
- 原子创建（O_CREAT|O_EXCL）抢锁；锁文件内容 {"pid","purpose","ts"}
- 持锁方须周期 heartbeat（仅 touch mtime，原子无撕裂读风险）
- 心跳超过 STALE_S（20 分钟）或持锁进程已死 → 视为遗留锁可抢占回收；
  M3 看门狗单条上限 600s < STALE_S，正常持锁不会被误抢
- 仅供长跑批处理使用；交互式/单次调用（门户问答等）不加锁，排队几秒可容忍
"""
import contextlib
import ctypes
import json
import os
import time

_ROOT = os.path.dirname(os.path.abspath(__file__))
LOCK_PATH = os.path.join(_ROOT, "locks", "ollama_batch.lock")
STALE_S = 1200            # 无心跳超此秒数视为遗留死锁
_POLL_S = 5


def _alive(pid):
    """Windows 安全的进程存活探测（勿用 os.kill——Windows 下会真杀进程）。"""
    if pid <= 0:
        return False
    try:
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            k32.GetExitCodeProcess(h, ctypes.byref(code))
            return code.value == 259                  # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    except Exception:
        return False      # 查询失败按死进程处理（宁可重抢，代价只是一轮并行）


def _try_create(purpose):
    os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
    try:
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps({"pid": os.getpid(), "purpose": purpose,
                            "ts": round(time.time())}))
    return True


def _stealable():
    """锁是否可抢占：持锁进程已死，或心跳超时。"""
    try:
        with open(LOCK_PATH, encoding="utf-8") as f:
            pid = int((json.load(f) or {}).get("pid") or 0)
    except Exception:
        return True                                    # 无锁/内容损坏
    if pid != os.getpid() and not _alive(pid):
        return True
    try:
        return (time.time() - os.path.getmtime(LOCK_PATH)) > STALE_S
    except OSError:
        return True


def acquire(purpose, wait_s=0):
    """抢锁；wait_s 内每 5s 重试，抢不到返回 False（调用方本轮让位）。"""
    deadline = time.time() + max(0, wait_s)
    while True:
        if _try_create(purpose):
            return True
        if _stealable():
            try:
                os.remove(LOCK_PATH)                   # 回收遗留锁后重试
            except OSError:
                pass
            if _try_create(purpose):
                return True
        if time.time() >= deadline:
            return False
        time.sleep(_POLL_S)


def heartbeat():
    """持锁方周期续命：仅 touch mtime（不动内容，避免撕裂读）。"""
    try:
        os.utime(LOCK_PATH, None)
    except OSError:
        pass


def release():
    """释放；锁已被他人抢占重建时不动别人的锁。"""
    try:
        with open(LOCK_PATH, encoding="utf-8") as f:
            pid = int((json.load(f) or {}).get("pid") or 0)
        if pid != os.getpid():
            return
    except Exception:
        pass
    try:
        os.remove(LOCK_PATH)
    except OSError:
        pass


@contextlib.contextmanager
def batch_lock(purpose, wait_s=0):
    """上下文管理器形态：yield 是否抢到。"""
    yield acquire(purpose, wait_s)
    release()
