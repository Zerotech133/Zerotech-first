# -*- coding: utf-8 -*-
"""P2.5 生产化：轻量内存限流（滑窗），供门户/客户 API 使用。"""
import threading
import time
from typing import Dict, List


class SlidingWindow:
    def __init__(self, limit: int, window: float = 60.0):
        self.limit = int(limit)
        self.window = float(window)
        self._hits: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        t = now if now is not None else time.time()
        with self._lock:
            arr = self._hits.setdefault(key, [])
            arr[:] = [x for x in arr if t - x < self.window]
            if len(arr) >= self.limit:
                return False
            arr.append(t)
            return True

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)


LOGIN = SlidingWindow(10, 60)      # 每 IP 登录
TRIAL = SlidingWindow(5, 60)       # 每 IP 试用申请
TRACK = SlidingWindow(120, 60)     # 每 IP 埋点
ANALYZE = SlidingWindow(5, 60)     # 每租户分析
JOB = SlidingWindow(120, 60)       # 每租户任务轮询
COMMERCIAL = SlidingWindow(60, 60)  # 每 IP 商业只读接口


def reset_all() -> None:
    for w in (LOGIN, TRIAL, TRACK, ANALYZE, JOB, COMMERCIAL):
        w.reset()
