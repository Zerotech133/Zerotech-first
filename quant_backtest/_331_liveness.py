# -*- coding: utf-8 -*-
"""331：判别 tdx_sync 65096 官方实例是卡死还是在跑——CPU 增量+TCP 连接+日志头部。"""

import subprocess
import time
import ctypes
import ctypes.wintypes as wt

PIDS = [65096, 74232, 46396]
k32 = ctypes.windll.kernel32


def cpu_seconds(pid):
    h = k32.OpenProcess(0x1000, False, pid)
    if not h:
        return None
    try:
        ct, et, kt, ut = wt.FILETIME(), wt.FILETIME(), wt.FILETIME(), wt.FILETIME()
        if not k32.GetProcessTimes(
            h, ctypes.byref(ct), ctypes.byref(et), ctypes.byref(kt), ctypes.byref(ut)
        ):
            return None
        return (
            (kt.dwHighDateTime << 32)
            + kt.dwLowDateTime
            + (ut.dwHighDateTime << 32)
            + ut.dwLowDateTime
        ) / 1e7
    finally:
        k32.CloseHandle(h)


r = subprocess.run(["netstat", "-ano"], capture_output=True)
nets = r.stdout.decode("gbk", errors="replace").splitlines()
conns = {p: [] for p in PIDS}
for l in nets:
    parts = l.split()
    if len(parts) >= 5 and parts[-1] in [str(p) for p in PIDS]:
        conns[int(parts[-1])].append(" ".join(parts[:4]))

snap1 = {p: cpu_seconds(p) for p in PIDS}
time.sleep(20)
snap2 = {p: cpu_seconds(p) for p in PIDS}

for p in PIDS:
    d = None
    if snap1[p] is not None and snap2[p] is not None:
        d = snap2[p] - snap1[p]
    print(
        "PID %d: cpu1=%.1fs cpu2=%.1fs delta=%.2fs conns=%s"
        % (
            p,
            snap1[p] or -1,
            snap2[p] or -1,
            d if d is not None else -1,
            conns[p][:3] if conns[p] else "none",
        )
    )

print("\n=== out log head (65096 writes at offset 0):")
head = open(r"C:\Temp\tdx_sync_out.log", "rb").read(600)
print(head.decode("utf-8", errors="replace"))
