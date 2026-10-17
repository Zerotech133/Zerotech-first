# -*- coding: utf-8 -*-
"""双服务看门狗：Hermes webui(8787) + CowAgent console(9899) 掉了就拉。

以 Bash 后台任务形态运行（任务在=守护在=服务在）。
WorkBuddy 沙箱内派生的进程会被会话 Job 连坐，唯一稳定形态是
「后台任务主命令常驻」——本守护即该形态；它拉起的子进程随守护存活。

拉起节奏：发现 DOWN 立即拉；启动宽限期内（hermes 60s / cow 40s）不重复拉；
宽限期后仍 DOWN 则每轮（30s）再拉，直到端口就绪。

日志：C:/Temp/keepalive.log（守护动作）/ hermes_webui.log / cowagent_bg.log（服务输出）
停止方式：停掉承载本脚本的后台任务即可（子进程随之由 Job 清理）。
"""
import os
import socket
import subprocess
import sys
import time
from datetime import datetime

# K-STOCK-414：旧机 E:\ / Administrator 硬编码换机即死（守护一崩双服务连坐
# 失守，2026-10-16 实测 8787/9899 双双下线）→ 全部按本文件位置/当前用户推导。
STATION = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根
HERMES_PS1 = os.path.join(STATION, "Hermes", "start-hermes.ps1")
HERMES_HOME = os.path.join(os.path.expanduser("~"), ".hermes")
HERMES_SERVER = os.path.join(STATION, "Hermes", "hermes-webui", "server.py")
HERMES_STATE = os.path.join(STATION, "Hermes", "webui-state")
COW_DIR = os.path.join(STATION, "CowAgent")
COW_PY = os.path.join(COW_DIR, "venv", "Scripts", "python.exe")

LOG_KA = r"C:\Temp\keepalive.log"
LOG_HERMES = r"C:\Temp\hermes_webui.log"
LOG_COW = r"C:\Temp\cowagent_bg.log"

DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

SERVICES = (
    # name, port, grace_s, spawner
    ("hermes", 8787, 90, "hermes"),
    ("cow", 9899, 40, "cow"),
)


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%m-%d %H:%M:%S')}] {msg}\n"
    try:
        with open(LOG_KA, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass
    sys.stdout.write(line)
    sys.stdout.flush()


def port_open(port: int) -> bool:
    s = socket.socket()
    s.settimeout(1.5)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def spawn(kind: str) -> None:
    # K-STOCK-414：spawn 整体包 try——旧实现 venv 缺失等 FileNotFoundError
    # 直接炸出 while True，另一服务连坐失守；现单服务故障仅留痕。
    try:
        _spawn(kind)
    except Exception as e:  # noqa: BLE001
        log(f"spawn {kind} FAILED: {e!r}")


def _spawn(kind: str) -> None:
    os.makedirs(os.path.dirname(LOG_KA), exist_ok=True)
    if kind == "hermes":
        # 纯 python 链（powershell 中继在沙箱内被静默掐死，2026-10-08 实测）；
        # 环境变量与 start-hermes.ps1 默认值一致
        env = dict(os.environ)
        # K-STOCK-414：删除旧机代理端口 54654 硬编码回退（本机无此代理，
        # 会把外部请求引向黑洞）；仅沿用已有代理变量，不凭空制造。
        env.update({
            "PYTHONUTF8": "1",
            "HERMES_HOME": HERMES_HOME,
            "HERMES_WEBUI_PYTHON": HERMES_HOME + r"\venv\Scripts\python.exe",
            "HERMES_WEBUI_HOST": "127.0.0.1",
            "HERMES_WEBUI_PORT": "8787",
            "HERMES_WEBUI_STATE_DIR": HERMES_STATE,
            "HERMES_WEBUI_AGENT_DIR": HERMES_HOME + r"\hermes-agent",
            "NO_PROXY": "localhost,127.0.0.1,itokengate.com",
        })
        logf = open(LOG_HERMES, "ab")
        logf.write(f"\n=== keepalive launch hermes {datetime.now()} ===\n".encode())
        logf.flush()
        subprocess.Popen(
            [HERMES_HOME + r"\venv\Scripts\python.exe", HERMES_SERVER],
            cwd=os.path.dirname(HERMES_SERVER),
            stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT,
            creationflags=DETACHED, close_fds=True, env=env)
        logf.close()  # K-STOCK-414：子进程已继承句柄，父侧及时回收
    else:
        logf = open(LOG_COW, "ab")
        logf.write(f"\n=== keepalive launch cowagent {datetime.now()} ===\n".encode())
        logf.flush()
        subprocess.Popen(
            [COW_PY, "app.py"],
            cwd=COW_DIR,
            stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT,
            creationflags=DETACHED, close_fds=True)
        logf.close()


def main() -> int:
    log(f"keepalive watchdog start (pid={os.getpid()})")
    up = {name: False for name, *_ in SERVICES}
    last_spawn = {name: 0.0 for name, *_ in SERVICES}
    while True:
        for name, port, grace, kind in SERVICES:
            if port_open(port):
                if not up[name]:
                    log(f"{name}:{port} UP")
                    up[name] = True
                continue
            now = time.time()
            if up[name]:
                log(f"{name}:{port} DOWN -> respawn")
                up[name] = False
                last_spawn[name] = now
                spawn(kind)
            elif now - last_spawn[name] > grace:
                log(f"{name}:{port} still DOWN after {grace}s grace -> respawn")
                last_spawn[name] = now
                spawn(kind)
            # 宽限期内：等它起来，不重复拉
        time.sleep(30)
    return 0


if __name__ == "__main__":
    sys.exit(main())
