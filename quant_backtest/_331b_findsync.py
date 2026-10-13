# -*- coding: utf-8 -*-
"""331b：定位 tdx_sync 进程并报告 CPU/内存（区分解析慢 vs 卡死）。"""
import subprocess
import sys
import time
import ctypes
import ctypes.wintypes as wt

k32 = ctypes.windll.kernel32


def cpu_and_mem(pid):
    h = k32.OpenProcess(0x1FFF, False, int(pid))  # +QUERY_LIMITED | VM_READ
    if not h:
        return None, None
    try:
        ct, et, kt, ut = wt.FILETIME(), wt.FILETIME(), wt.FILETIME(), wt.FILETIME()
        if not k32.GetProcessTimes(h, ctypes.byref(ct), ctypes.byref(et),
                                   ctypes.byref(kt), ctypes.byref(ut)):
            return None, None
        cpu = ((kt.dwHighDateTime << 32) + kt.dwLowDateTime +
               (ut.dwHighDateTime << 32) + ut.dwLowDateTime) / 1e7
        pmc = (ctypes.c_size_t * 9)()  # PROCESS_MEMORY_COUNTERS
        pmc[0] = ctypes.sizeof(pmc)
        psapi = ctypes.WinDLL("psapi")
        mem = psapi.GetProcessMemoryInfo(h, pmc, ctypes.sizeof(pmc))
        return cpu, (pmc[2] // 1024) if mem else -1  # WorkingSetSize KB
    finally:
        k32.CloseHandle(h)


r = subprocess.run(["tasklist", "/FO", "CSV"], capture_output=True)
cands = []
for l in r.stdout.decode("gbk", errors="replace").splitlines()[1:]:
    p = [x.strip('"') for x in l.split('","')]
    if len(p) >= 2 and p[0] == "python.exe":
        cands.append(p[1])

sys.path.insert(0, r"E:\老田的AI助手\quant_backtest")
from _331_readcmdline import cmdline_of  # noqa: E402

sync_pids = []
for pid in cands:
    cl = cmdline_of(int(pid))
    if cl and "tdx_sync" in cl:
        sync_pids.append(pid)
        c0, m0 = cpu_and_mem(int(pid))
        print("pid %s cpu=%.1fs ws=%dKB cmd=%s" % (pid, c0 or -1, m0 or -1, cl[:90]))

if sync_pids:
    time.sleep(20)
    for pid in sync_pids:
        c1, m1 = cpu_and_mem(int(pid))
        print("after20s pid %s cpu=%.1fs ws=%dKB" % (pid, c1 or -1, m1 or -1))
else:
    print("no tdx_sync process found")
