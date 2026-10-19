# -*- coding: utf-8 -*-
"""FeishuBotWatchdog 拉起器：无 feishu_bot_ws 进程则 DETACHED 启动。
计划任务每 5 分钟调本脚本（SYSTEM 上下文）；幂等，已有实例则直接退出。
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# K-STOCK-413：venv 依赖为 cp313 编译（Python311 装不下）→ 用 CowAgent venv
PY = os.path.join(os.path.dirname(HERE), "CowAgent", "venv", "Scripts", "python.exe")
BOT = os.path.join(HERE, "feishu_bot_ws.py")
LOGD = os.path.join(HERE, "logs")
LOG = os.path.join(LOGD, "feishu_bot_out.log")


def running():
    # wmic 已移除 → PowerShell Get-CimInstance 查命令行匹配 feishu_bot_ws
    # （审核#4：原 tasklist 快路径在本机恒有 15+ python.exe 常驻，n==0 永不
    # 成立=死代码，已删；命令行匹配才是唯一可靠判据）
    # K-PUSH-004 判例（2026-10-17）：SYSTEM 上下文 CIM 偶发慢/超时，此前
    # TimeoutExpired 未捕获 → start 无声崩溃（Popen bot 未执行且零留痕，
    # 正是审核#1 所防的零痕迹瘫痪）。异常一律按「未运行」处理，宁可重拉，
    # 由 guardian 下跳 dedup（树根判据）收敛偶发双开。
    try:
        r2 = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\")"
             "|Where-Object{$_.CommandLine -like '*feishu_bot_ws*'}"
             "|Select-Object -First 1 ProcessId|Format-List"],
            capture_output=True, timeout=30)
    except Exception as e:
        _fail("running() probe failed (treat as not-running): %r" % e)
        return False
    out = (r2.stdout or b"").decode("gbk", "replace")
    for line in out.splitlines():
        if "ProcessId" in line and line.split(":")[-1].strip().isdigit():
            return True
    return False


def _fail(msg):
    # 审核#1/#2：watchdog 自身故障必须落 out.log——start 脚本 stdout/stderr 由
    # Task Scheduler 丢弃，不留痕=零痕迹瘫痪（venv 基座被 WorkBuddy 清理即此链）
    with open(LOG, "ab") as fo:
        fo.write(("%s watchdog: %s\n" % (
            time.strftime("%m-%d %H:%M:%S"), msg)).encode("utf-8", "replace"))


def main():
    os.makedirs(LOGD, exist_ok=True)
    if not os.path.exists(PY):
        _fail("python missing: %s" % PY)  # 审核#1 最小修：基座失效留痕
        return
    if running():
        print("already running")
        return
    # K-STOCK-122 M7 白名单：止盈止损指令仅限老田（env 注入子进程）
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "news_collector"))
    import push_targets  # K-STOCK-403：收件人单一事实源
    env = dict(os.environ)
    env.setdefault("EXIT_ALLOW_OPEN_IDS",
                   push_targets.target_id("个人") or "")  # 老田 bot 域 open_id
    try:
        with open(LOG, "ab") as fo:
            # D2（N11 启动链 utf8）：-X utf8 令子进程 stdout/stderr 以
            # UTF-8 写出（api_common.py:427 alertpush 判例同款），修复
            # 启动链中文日志乱码；stdout=fo 为二进制追加，字节流即
            # UTF-8，_fail() 写入亦显式 utf-8/replace，全链编码一致。
            subprocess.Popen(
                [PY, "-X", "utf8", "-u", BOT], stdin=subprocess.DEVNULL, stdout=fo,
                stderr=subprocess.STDOUT, env=env,
                creationflags=(subprocess.DETACHED_PROCESS
                               | subprocess.CREATE_NEW_PROCESS_GROUP))
    except OSError as e:  # 审核#2：Popen 失败原走无人接收的 stderr
        _fail("launch failed: %r" % e)
        return
    time.sleep(2)
    print("launched")


if __name__ == "__main__":
    main()
