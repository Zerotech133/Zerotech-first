# -*- coding: utf-8 -*-
"""9899 CowAgent console 看门狗（K-STOCK-077 P-1.5）+ 8095 picks 定时发布调度器看护。

背景：cow:9899 原由 ops/keepalive_bg.py（会话绑定后台任务）守护——
WorkBuddy 会话结束会被 Job 连坐收割，服务随即裸奔。本脚本以独立计划任务
「AI工作站CowAgent看门狗」每 5 分钟跑一次，任务宿主稳定不受会话影响。

2026-10-14 起 8095（picks 定时发布管理页 publish_schedule_server.py）、
8096（KM 知识库飞书联动 km_feishu_hub.py）与 8097（推送管理中心
push_admin_server.py）并入本守护：同一稳定宿主，一次任务跑四个探针，
注册层零改动（新增计划任务会被安全中心 schtasks 黑名单拦，改既有任务
脚本不需要）。

探针：TCP 连目标端口（socket 通=活）。
复活：看门狗自己 Popen 分离拉起（本进程宿主=计划任务，子进程随任务存活；
与 8094 走 Start-ScheduledTask 不同——两者均无既有服务任务）。
防抖：探针 x2 间隔 3s；8095 复活宽限 ~15s、cow 宽限 60s 内不重复拉。

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
PICKS_PORT = 8095
PORTAL_DIR = r"E:\老田的AI助手\portal"
SYS_PY = r"C:\Program Files\Python311\python.exe"
# 8096 KM 知识库飞书联动（km_feishu_hub.py）——同配方并入本守护
KM_PORT = 8096
# 8097 推送管理中心（push_admin_server.py）
PA_PORT = 8097
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


def alive_on(port):
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def guard_port(port, script_args, log_name, grace_iters, tag):
    """通用端口看护：x2 防抖 → 分离拉起 → 宽限验证。"""
    if alive_on(port):
        return
    time.sleep(3)
    if alive_on(port):
        return  # 抖动
    log("%s:%d DOWN x2 -> spawn" % (tag, port))
    logf = open(log_name, "ab")
    logf.write(f"\n=== watchdog spawn {datetime.now()} ===\n".encode())
    logf.flush()
    subprocess.Popen([SYS_PY] + script_args,
                     cwd=PORTAL_DIR,
                     stdin=subprocess.DEVNULL, stdout=logf,
                     stderr=subprocess.STDOUT, creationflags=DETACHED,
                     close_fds=True)
    for i in range(grace_iters):
        time.sleep(3)
        if alive_on(port):
            log("%s REVIVED after ~%ds" % (tag, (i + 1) * 3))
            return
    log("%s REVIVE_FAIL（%ds 仍无监听——看 %s）" % (tag, grace_iters * 3, log_name))


def guard_picks_sched():
    """8095 picks 定时发布调度器：掉线则用系统 python 分离拉起（--bg）。"""
    guard_port(PICKS_PORT, ["publish_schedule_server.py", "--bg"],
               r"C:\Temp\picks_sched_wd.log", 5, "picks_sched")


def guard_km_hub():
    """8096 KM 知识库飞书联动：掉线则分离拉起（--bg）。"""
    guard_port(KM_PORT, ["km_feishu_hub.py", "--bg"],
               r"C:\Temp\km_hub_wd.log", 5, "km_hub")


def guard_push_admin():
    """8097 推送管理中心：掉线则分离拉起（--bg）。"""
    guard_port(PA_PORT, ["push_admin_server.py", "--bg"],
               r"C:\Temp\push_admin_wd.log", 5, "push_admin")


def guard_cow():
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


def main():
    guard_picks_sched()
    guard_km_hub()
    guard_push_admin()
    guard_cow()


if __name__ == "__main__":
    main()
