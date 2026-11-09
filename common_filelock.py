# -*- coding: utf-8 -*-
"""
common_filelock.py — 本工程公共文件锁 + 安全写 JSON 工具
背景判例 2026-11-09：多进程共写同一 json（tmp→rename）在 Windows 上互撞，
PermissionError 导致缠论 healthcheck H1 闸门 FAIL。

用法一（锁住任意临界区）：
    from common_filelock import FileLock
    with FileLock(r"D:/data/xxx.json", timeout=30):
        ... 读写 xxx.json ...

用法二（一键安全写 json）：
    from common_filelock import write_json_safe
    write_json_safe(path, obj)   # 内部：持锁 + 唯一 tmp(pid) + os.replace

约定：
- 锁文件为 <目标>.lock，与目标同目录，长期保留（勿手工删，删了会瞬时失去互斥）。
- Windows 用 msvcrt 区域锁；POSIX 兜底 fcntl。跨沙箱/跨进程均有效。
"""
import json
import os
import time

try:
    import msvcrt  # Windows

    def _lock_fd(fd):
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock_fd(fd):
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
except ImportError:  # POSIX 兜底
    import fcntl

    def _lock_fd(fd):
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_fd(fd):
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass


class FileLock:
    """阻塞式进程互斥锁（timeout 秒内抢不到即抛 TimeoutError）。"""

    def __init__(self, target_path, timeout=30.0, poll=0.15):
        self.target = os.path.abspath(target_path)
        self.lock_path = self.target + ".lock"
        self.timeout = float(timeout)
        self.poll = float(poll)
        self._fd = None

    def acquire(self):
        os.makedirs(os.path.dirname(self.lock_path) or ".", exist_ok=True)
        # "a" 打开：文件存在与否都可用，且不截断他人内容
        self._fd = open(self.lock_path, "a")
        deadline = time.time() + self.timeout
        while True:
            try:
                self._fd.seek(0)
                _lock_fd(self._fd.fileno())
                return self
            except OSError:
                if time.time() >= deadline:
                    self._fd.close()
                    self._fd = None
                    raise TimeoutError(
                        "FileLock timeout(%ss): %s" % (self.timeout, self.lock_path)
                    )
                time.sleep(self.poll)

    def release(self):
        if self._fd is not None:
            try:
                self._fd.seek(0)
                _unlock_fd(self._fd.fileno())
            finally:
                self._fd.close()
                self._fd = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False


def write_json_safe(path, obj, timeout=30.0, ensure_ascii=False):
    """持锁 + 唯一 tmp(pid) + 原子替换。多进程/多线程共写一文件的安全出口。"""
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=ensure_ascii)
    with FileLock(path, timeout=timeout):
        os.replace(tmp, path)
    return path


def _selftest_worker(n, tgt):
    """自测子进程体（必须模块顶层定义，Windows spawn 才能 pickle）。"""
    for i in range(50):
        with FileLock(tgt, timeout=30):
            try:
                with open(tgt, encoding="utf-8") as f:
                    d = json.load(f)
            except Exception:
                d = {"count": 0}
            d["count"] += 1
            with open(tgt, "w", encoding="utf-8") as f:
                json.dump(d, f)


if __name__ == "__main__":
    # 自测：并发 8 进程各写 50 次，终文件必须为合法 json 且计数精确
    import multiprocessing as mp

    tgt = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "_filelock_selftest.json")
    ps = [mp.Process(target=_selftest_worker, args=(k, tgt)) for k in range(8)]
    [p.start() for p in ps]
    [p.join() for p in ps]
    with open(tgt, encoding="utf-8") as f:
        d = json.load(f)
    assert d["count"] == 400, "SELFTEST FAIL: count=%s" % d["count"]
    os.remove(tgt)
    os.remove(tgt + ".lock")
    print("FILELOCK SELFTEST PASS: 8 进程 x 50 次 = 400 计数精确无丢失")
