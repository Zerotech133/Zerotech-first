# -*- coding: utf-8 -*-
"""9899 CowAgent console 看门狗（K-STOCK-077 P-1.5）。

背景：cow:9899 原由 ops/keepalive_bg.py（会话绑定后台任务）守护——
WorkBuddy 会话结束会被 Job 连坐收割，服务随即裸奔。本脚本以独立计划任务
「AI工作站CowAgent看门狗」每 5 分钟跑一次，任务宿主稳定不受会话影响。

探针：TCP 连 127.0.0.1:9899（console 无稳定 HTTP health，socket 通=活）。
复活：看门狗自己 Popen 分离拉起 CowAgent（本进程宿主=计划任务，子进程
随任务存活；与 8094 走 Start-ScheduledTask 不同——Cow 无既有服务任务）。
防抖：探针 x2 间隔 3s；启动宽限 60s 内不重复拉。

日志：C:/Temp/watchdog_9899.log（只记动作，静默通过不写）。
"""
import os
import socket
import subprocess
import sys
import time
from datetime import datetime

PORT = 9899
COW_DIR = r"E:\老田的AI助手\CowAgent"
COW_PY = COW_DIR + r"\venv\Scripts\python.exe"
LOG = r"C:\Temp\watchdog_9899.log"
DETACHED = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED|NEW_GROUP|NO_WINDOW


def log(msg):
    line = "[%s] %s\n" % (datetime.now().strftime("%m-%d %H:%M:%S"), msg)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass
    print(line, end="")


def alive():
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect(("127.0.0.1", PORT))
        return True
    except OSError:
        return False
    finally:
        s.close()


def main():
    if alive():
        sys.exit(0)
    time.sleep(3)
    if alive():
        sys.exit(0)  # 抖动
    log("DOWN x2 -> spawn CowAgent")
    logf = open(r"C:\Temp\cowagent_wd.log", "ab")
    logf.write(f"\n=== watchdog spawn {datetime.now()} ===\n".encode())
    logf.flush()
    subprocess.Popen([COW_PY, "app.py"], cwd=COW_DIR,
                     stdin=subprocess.DEVNULL, stdout=logf,
                     stderr=subprocess.STDOUT, creationflags=DETACHED,
                     close_fds=True)
    for i in range(20):  # 宽限 60s
        time.sleep(3)
        if alive():
            log("REVIVED after ~%ds" % ((i + 1) * 3))
            sys.exit(0)
    log("REVIVE_FAIL（60s 仍无监听——看 C:/Temp/cowagent_wd.log）")
    sys.exit(1)


if __name__ == "__main__":
    main()
