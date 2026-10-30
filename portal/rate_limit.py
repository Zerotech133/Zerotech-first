# -*- coding: utf-8 -*-
"""P2.5 生产化：轻量内存限流（滑窗），供门户/客户 API 使用。"""
import os
import sqlite3
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


class SqliteSlidingWindow:
    """SQLite 滑窗限流：多个进程/实例共享同一 DB 文件（多实例部署用）。"""

    def __init__(self, kind: str, limit: int, window: float = 60.0, db: str | None = None):
        self.kind = kind
        self.limit = int(limit)
        self.window = float(window)
        self.db = db or os.environ.get("RATE_DB") or os.path.join(
            os.environ.get("COMMERCIAL_HOME") or r"C:\项目\commercial", "rate.db")
        self._lock = threading.Lock()

    def _conn(self):
        os.makedirs(os.path.dirname(self.db), exist_ok=True)
        c = sqlite3.connect(self.db, timeout=10)
        c.executescript("""CREATE TABLE IF NOT EXISTS rate_hits (
            kind TEXT, key TEXT, ts REAL); CREATE INDEX IF NOT EXISTS idx_rate ON rate_hits(kind,key,ts);""")
        return c

    def allow(self, key: str, now: float | None = None) -> bool:
        t = now if now is not None else time.time()
        with self._lock:
            c = self._conn()
            try:
                c.execute("DELETE FROM rate_hits WHERE kind=? AND key=? AND ts < ?",
                          (self.kind, key, t - self.window))
                n = c.execute("SELECT COUNT(*) FROM rate_hits WHERE kind=? AND key=?",
                              (self.kind, key)).fetchone()[0]
                if n >= self.limit:
                    c.commit()
                    return False
                c.execute("INSERT INTO rate_hits(kind,key,ts) VALUES(?,?,?)", (self.kind, key, t))
                c.commit()
                return True
            finally:
                c.close()

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            c = self._conn()
            try:
                if key is None:
                    c.execute("DELETE FROM rate_hits WHERE kind=?", (self.kind,))
                else:
                    c.execute("DELETE FROM rate_hits WHERE kind=? AND key=?", (self.kind, key))
                c.commit()
            finally:
                c.close()
