# -*- coding: utf-8 -*-
"""锁隔离对照：overview 重建期间，/api/health 是否被连坐。

复刻两种 handler 写法（不启服务，只在同进程里跑并发）：
  old = "with _lock: build_overview()"   —— 原实现，重建全程持共享锁
  new = "overview_cached()"              —— 新实现，只碰 overview 专用锁
同时另起 N 个线程持续做 "with _lock: 读缓存"（等同 /api/health 的关键区），
统计它在这段时间里的等待时长。
"""
import statistics
import sys
import threading
import time

sys.path.insert(0, r"C:\项目\老田的AI助手\portal")

import api_health_portal as H          # noqa: E402
import api_misc as M                  # noqa: E402

N_HEALTH = 6
health_lat = []
stop = threading.Event()


def health_worker():
    while not stop.is_set():
        t0 = time.perf_counter()
        with H._lock:                 # /api/health 的最小关键区
            pass
        health_lat.append(time.perf_counter() - t0)
        time.sleep(0.005)


def run_case(name, build):
    global health_lat
    health_lat = []
    stop.clear()
    hs = [threading.Thread(target=health_worker, daemon=True) for _ in range(N_HEALTH)]
    for t in hs:
        t.start()
    time.sleep(0.3)                   # 先测一段"无重建"的底噪
    base_n = len(health_lat)
    base = sorted(health_lat)[:base_n]

    t0 = time.perf_counter()
    build()
    build_s = time.perf_counter() - t0
    time.sleep(0.1)
    stop.set()
    for t in hs:
        t.join(timeout=1.0)

    during = sorted(health_lat[base_n:])
    p95 = during[int(0.95 * (len(during) - 1))] if during else float("nan")
    blocked = sum(1 for v in during if v > 0.05)     # >50ms 视为被堵住
    print("%-4s 构建 %6.3fs | health 样本 %5d | p95 %6.1fms | max %8.1fms | 被堵(>50ms) %3d 次 (%.1f%%)"
          % (name, build_s, len(during), p95 * 1000,
             (during[-1] * 1000 if during else float("nan")),
             blocked, 100.0 * blocked / len(during) if during else 0))
    return build_s, (during[-1] if during else 0.0)


def build_old():
    """原实现：持共享 _lock 重建全量。"""
    with H._lock:
        M.build_overview()


def build_new():
    """新实现：不碰共享 _lock。"""
    M.overview_cached()


print("=== 对照 1：冷启（缓存空）===")
M._OV_LIGHT["data"] = None
M._OV_LIGHT["t"] = 0.0
M._OV_HEAVY["data"] = None
M._OV_HEAVY["t"] = 0.0
old_s, old_max = run_case("old", build_old)

M._OV_LIGHT["data"] = None
M._OV_LIGHT["t"] = 0.0
M._OV_HEAVY["data"] = None
M._OV_HEAVY["t"] = 0.0
new_s, new_max = run_case("new", build_new)

print()
print("判定：health 最长阻塞由 %.0fms 降到 %.0fms"
      % (old_max * 1000, new_max * 1000))
