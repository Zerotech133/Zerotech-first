# -*- coding: utf-8 -*-
"""注册 FeishuBotWatchdog SYSTEM 计划任务（每 5 分钟自愈拉起飞书机器人）。

与 ZhiceWatchdog 同配方：SYSTEM 上下文在会话之外拉进程，天然避开
终端会话 Job Object 对 DETACHED 子进程的清理。
幂等：/F 覆盖重建。用法：
    python register_feishu_watchdog_task.py          # 注册并立即触发一次
    python register_feishu_watchdog_task.py --no-run # 仅注册
"""

import os
import subprocess
import sys
import time

TASK = "FeishuBotWatchdog"
# 审核#3：绝对路径硬编码改 __file__ 相对推导——仓库迁移后重跑本脚本即恢复
HERE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(os.path.dirname(HERE), "CowAgent", "venv", "Scripts", "python.exe")
BOT = os.path.join(HERE, "start_feishu_bot.py")


def run(cmd, timeout=60):
    r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    out = (r.stdout or b"").decode("gbk", "replace").strip()
    err = (r.stderr or b"").decode("gbk", "replace").strip()
    return r.returncode, out, err


def main():
    no_run = "--no-run" in sys.argv
    tr = f'"{PY}" "{BOT}"'
    cmd = [
        "schtasks",
        "/Create",
        "/TN",
        TASK,
        "/TR",
        tr,
        "/SC",
        "MINUTE",
        "/MO",
        "5",
        "/RU",
        "SYSTEM",
        "/RL",
        "HIGHEST",
        "/F",
    ]
    print("[1] register:", " ".join(cmd[:6]), "...")
    rc, out, err = run(cmd)
    print("    rc=", rc, "|", out or err)
    if rc != 0:
        print("FAILED-REGISTER")
        return 1
    # 复核：查询任务状态
    rc, out, err = run(["schtasks", "/Query", "/TN", TASK, "/FO", "LIST"])
    keep = [
        l
        for l in out.splitlines()
        if any(
            k in l
            for k in (
                "TaskName",
                "Status",
                "任务名",
                "状态",
                "Next Run",
                "下次",
                "模式",
                "Logon",
            )
        )
    ]
    print("[2] query rc=", rc)
    for l in keep:
        print("   ", l.strip())
    if no_run:
        return 0
    # 立即触发一次
    print("[3] run-now")
    rc, out, err = run(["schtasks", "/Run", "/TN", TASK])
    print("    rc=", rc, "|", out or err)
    time.sleep(6)
    rc, out, err = run(["schtasks", "/Query", "/TN", TASK, "/FO", "LIST"])
    for l in out.splitlines():
        if "Status" in l or "状态" in l:
            print("[4] after-run", l.strip())
    print("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
