# -*- coding: utf-8 -*-
"""331：读 TdxSync 计划任务缓存状态（最后运行时间/结果/标志）。"""
import time
import winreg

BASE = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Schedule\TaskCache"
k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, BASE + r"\Tree\TdxSync")
guid = winreg.QueryValueEx(k, "Id")[0]
print("GUID:", guid)
k2 = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, BASE + "\\Tasks\\" + guid)
i = 0
while True:
    try:
        name, val, _ = winreg.EnumValue(k2, i)
        i += 1
        if name in ("LastRunTime", "LastTaskResult", "NumberOfMissedRuns",
                    "Flags", "State", "NextRunTime"):
            if name in ("LastRunTime", "NextRunTime") and isinstance(val, int):
                # FILETIME (100ns since 1601-01-01)
                ft = val
                unix = (ft - 116444736000000000) / 10_000_000
                val = "%s (unix %.0f)" % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(unix)), unix)
        print(name, "=", val)
    except OSError:
        break
