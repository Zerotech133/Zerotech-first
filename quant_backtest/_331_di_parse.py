# -*- coding: utf-8 -*-
"""331：解析 TdxSync DynamicInfo 的时间戳（LastRun 等）。"""

import time
import winreg

BASE = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Schedule\TaskCache"
k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, BASE + r"\Tree\TdxSync")
guid = winreg.QueryValueEx(k, "Id")[0]
k2 = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, BASE + "\\Tasks\\" + guid)
di = winreg.QueryValueEx(k2, "DynamicInfo")[0]
print("DynamicInfo hex:", di.hex())


def ft_to_str(ft):
    unix = (ft - 116444736000000000) / 10_000_000
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(unix))


# 滑窗解所有 8 字节对齐的 FILETIME，落在合理区间的都打印
import struct

for off in range(0, len(di) - 8):
    (ft,) = struct.unpack_from("<Q", di, off)
    if 134000000000000000 < ft < 140000000000000000:  # ~2025-2027 年区间
        try:
            s = ft_to_str(ft)
            if "2026-10" in s or "2026-09" in s:
                print("off=%d ft=%s -> %s" % (off, hex(ft), s))
        except (OverflowError, OSError, ValueError):
            pass
