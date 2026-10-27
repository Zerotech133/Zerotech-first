# -*- coding: utf-8 -*-
"""K-STOCK-304 C 项评估日提醒（一次性，2026-10-20 09:00 由计划任务拉起）。

发飞书私聊提醒评估「推送出口统一到量化问答机器人」，发送后自删计划任务。
依据：docs/plans/2026-10-13_推送联动收口_K-STOCK-304.md §C 挂账决定。
"""

import os
import subprocess
import sys

TASK = "K-STOCK-304-C-Eval"
# K-PUSH-001 P0：换机路径残留（旧机 E 盘）→ __file__ 动态推导
SEND_FEISHU = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "news_collector",
    "send_feishu.py",
)
ALERT_FILE = r"C:\Temp\push_eval_reminder.txt"

MSG = (
    "【K-STOCK-304 · C 项评估日】推送出口统一到「量化问答机器人」的观察期"
    "已满一周。评估输入：C:\\Temp\\push_liveness.log（漏推/误报）、"
    "pg_robot_watchdog.log + pg_robot_system.log（稳定性）、"
    "以及你对新旧 bot 认知混淆是否仍构成困扰。"
    "要评估/启动 C 项，对任意 Agent 说「评估 K-STOCK-304 C 项」。"
)


def main():
    try:
        with open(ALERT_FILE, "w", encoding="utf-8") as f:
            f.write(MSG)
        r = subprocess.run(
            [sys.executable, SEND_FEISHU, ALERT_FILE],
            capture_output=True,
            text=True,
            timeout=60,
        )
        ok = "SEND_OK" in (r.stdout or "")
        print("remind sent:", ok)
    except (OSError, subprocess.SubprocessError) as e:
        print("remind EXC:", e)
        ok = False
    # 自删（一次性任务语义；发送失败也删——观察期日志仍在，评估可随时手动做）
    subprocess.run(
        ["schtasks", "/Delete", "/TN", TASK, "/F"], capture_output=True, timeout=30
    )
    print("task removed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
