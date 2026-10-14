# -*- coding: utf-8 -*-
"""331：列出 powershell.exe / python.exe 进程及启动时间（定位 17:43 挂起实例）。"""
import ctypes
import ctypes.wintypes as wt
import subprocess

# 拿 PID 列表（tasklist 输出 GBK）
r = subprocess.run(["tasklist", "/FO", "CSV"], capture_output=True)
targets = {}
for line in r.stdout.decode("gbk", errors="replace").splitlines()[1:]:
    parts = [p.strip('"') for p in line.split('","')]
    if len(parts) >= 2 and parts[0].lower() in ("powershell.exe", "python.exe", "pythonw.exe", "cmd.exe"):
        try:
            targets[int(parts[1])] = parts[0]
        except ValueError:
            pass

k32 = ctypes.windll.kernel32
TOKEN = 0x1FFF  # PROCESS_QUERY_LIMITED_INFORMATION 0x1000 | QUERY_INFORMATION 0x0400
now = ctypes.wintypes.FILETIME()


def creation_time(pid):
    h = k32.OpenProcess(0x1000, False, pid)
    if not h:
        return None
    try:
        ct, et, kt, ut = wt.FILETIME(), wt.FILETIME(), wt.FILETIME(), wt.FILETIME()
        if not k32.GetProcessTimes(h, ctypes.byref(ct), ctypes.byref(et),
                                   ctypes.byref(kt), ctypes.byref(ut)):
            return None
        ft = (ct.dwHighDateTime << 32) | ct.dwLowDateTime
        unix = (ft - 116444736000000000) / 10_000_000
        import time
        return time.strftime("%m-%d %H:%M:%S", time.localtime(unix))
    finally:
        k32.CloseHandle(h)


rows = []
for pid, name in sorted(targets.items()):
    ct = creation_time(pid)
    rows.append((ct, pid, name))
for ct, pid, name in sorted(rows, reverse=True)[:25]:
    print("%s  %-6s %s" % (ct or "OpenFail", pid, name))
