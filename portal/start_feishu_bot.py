# -*- coding: utf-8 -*-
"""FeishuBotWatchdog 拉起器：无 feishu_bot_ws 进程则 DETACHED 启动。
计划任务每 5 分钟调本脚本（SYSTEM 上下文）；幂等，已有实例则直接退出。
"""
import os
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = r"C:/Program Files/Python311/python.exe"
BOT = os.path.join(HERE, "feishu_bot_ws.py")
LOGD = os.path.join(HERE, "logs")
LOG = os.path.join(LOGD, "feishu_bot_out.log")


def running():
    # 中文 Windows 下 tasklist 输出为 GBK，text=True 默认 utf-8 会炸解码 → 手动 gbk 解码
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq python.exe",
                        "/FO", "CSV"], capture_output=True)
    n = (r.stdout or b"").decode("gbk", "replace").count("python.exe")
    if n == 0:
        return False
    # wmic 已移除 → PowerShell Get-CimInstance 查命令行匹配 feishu_bot_ws
    r2 = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\")"
         "|Where-Object{$_.CommandLine -like '*feishu_bot_ws*'}"
         "|Select-Object -First 1 ProcessId|Format-List"],
        capture_output=True, timeout=30)
    out = (r2.stdout or b"").decode("gbk", "replace")
    for line in out.splitlines():
        if "ProcessId" in line and line.split(":")[-1].strip().isdigit():
            return True
    return False


def main():
    os.makedirs(LOGD, exist_ok=True)
    if running():
        print("already running")
        return
    with open(LOG, "ab") as fo:
        subprocess.Popen(
            [PY, "-u", BOT], stdin=subprocess.DEVNULL, stdout=fo,
            stderr=subprocess.STDOUT,
            creationflags=(subprocess.DETACHED_PROCESS
                           | subprocess.CREATE_NEW_PROCESS_GROUP))
    time.sleep(2)
    print("launched")


if __name__ == "__main__":
    main()
