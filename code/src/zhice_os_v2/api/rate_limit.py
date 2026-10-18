"""登录失败限流（进程内，轻量）。

上线前安全审计：/auth/login 无限流→暴力破解/撞库（涉及真实资金账户）。
按 (client_ip, username) 计数：滑动窗口内失败达阈值即锁定一段时间。

定位：应用层第一道防线（单进程/单 worker 生效即可）；生产另有 nginx limit_req
作第二层。时间用 time.monotonic()（不受系统时钟回拨影响）。
"""
from __future__ import annotations

import threading
import time


class LoginThrottle:
    def __init__(
        self, *, max_fails: int = 5, window_s: float = 300.0, lockout_s: float = 900.0
    ) -> None:
        self._max = max_fails
        self._window = window_s
        self._lockout = lockout_s
        self._state: dict[str, list[float]] = {}  # key -> [fails, window_start, locked_until]
        self._lock = threading.Lock()

    def locked_remaining(self, key: str, now: float | None = None) -> float:
        """当前是否锁定；返回剩余锁定秒数（0=未锁定）。"""
        now = time.monotonic() if now is None else now
        with self._lock:
            st = self._state.get(key)
            if st and st[2] > now:
                return st[2] - now
            return 0.0

    def record_fail(self, key: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            st = self._state.get(key)
            if st is None or now - st[1] > self._window:
                st = [0.0, now, 0.0]  # 窗口过期→重置计数
            st[0] += 1
            if st[0] >= self._max:
                st[2] = now + self._lockout
            self._state[key] = st

    def record_success(self, key: str) -> None:
        with self._lock:
            self._state.pop(key, None)

    def reset(self) -> None:
        """测试用：清空全部状态。"""
        with self._lock:
            self._state.clear()


# 全局单例（登录路由共用）。
login_throttle = LoginThrottle()
