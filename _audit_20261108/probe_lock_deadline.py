# -*- coding: utf-8 -*-
"""证明：deadline 用 time.time() 时，墙钟被冻结 → 重试循环永不退出。

复刻 os_goal_store._file_lock 的两个版本（old=time.time / new=time.monotonic），
把 os.open 换成永远抛 PermissionError，再冻结 time.time，各跑 3 秒看是否返回。
"""
import threading
import time

TIMEOUT = 0.05
FAKE_NOW = 1000.0
REAL_TIME_TIME = time.time


def _spin(clock, os_open, result):
    """clock=取时刻的函数；os_open=打开函数（这里永远失败）。"""
    deadline = clock() + TIMEOUT
    while True:
        try:
            os_open()
            return
        except PermissionError:
            if clock() > deadline:
                result.append("gave_up")
                return
            time.sleep(0.05)


def run_case(name, clock, real_time_time):
    result = []
    th = threading.Thread(
        target=_spin, args=(clock, lambda: (_ for _ in ()).throw(PermissionError(13, "denied")), result),
        daemon=True)
    th.start()
    th.join(3.0)
    print("%-22s -> %s" % (name, result[0] if result else "HANG（3s 内未退出）"))
    return bool(result)


real_monotonic = time.monotonic

# 旧实现：墙钟；把 time.time 冻结
time.time = lambda: FAKE_NOW
try:
    old_ok = run_case("old(time.time)", time.time, time.time)
finally:
    time.time = REAL_TIME_TIME

# 新实现：单调钟，不受墙钟影响（墙钟仍冻结着也无所谓）
time.time = lambda: FAKE_NOW
try:
    new_ok = run_case("new(time.monotonic)", time.monotonic, time.time)
finally:
    time.time = REAL_TIME_TIME

print()
print("旧实现退出:", old_ok, "| 新实现退出:", new_ok)
print("结论:", "复现成功，monotonic 修复有效" if (not old_ok and new_ok) else "未复现，需再查")
