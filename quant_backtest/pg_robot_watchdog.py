# -*- coding: utf-8 -*-
"""PG-Robot 看门狗（K-STOCK-302）

由计划任务 PG-Robot-Watchdog 每 5 分钟拉起一次，run-and-exit：
  检测1 进程存活：无 p_g_robot 进程 → schtasks 重启 + 告警
  检测2 WS 卡死：日志尾 reconnecting/disconnected 之后无 connected
        且超过 STUCK_MIN 分钟 → schtasks 重启 + 告警
  正常 → 静默（watchdog 自身日志记一行心跳）
  告警走 news_collector/send_feishu.py（OPS-1001d 判例通道，私聊直达手机），
  同因 30 分钟冷却去重。

Task Scheduler 命令行 100% ASCII（system-task-deploy 判例：中文路径在
命令行会物理损坏），故执行副本部署于 C:\\aios\\quant_backtest\\。
自测：python pg_robot_watchdog.py --selftest（离线用例，不动生产）
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
import logging

TASK_NAME = "PG-Robot"
ROBOT_LOG = r"C:\Temp\pg_robot_system.log"
WD_LOG = r"C:\Temp\pg_robot_watchdog.log"
STATE_FILE = r"C:\Temp\pg_robot_watchdog_state.json"
ALERT_FILE = r"C:\Temp\pg_robot_watchdog_alert.txt"
# K-PUSH-001 P0：换机路径残留（旧机 E 盘）→ __file__ 动态推导
SEND_FEISHU = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "news_collector",
    "send_feishu.py",
)
STUCK_MIN = 5  # 重连卡死判定阈值（分钟）
ALERT_COOLDOWN_SEC = 1800  # 同因告警冷却（30 分钟）
VERIFY_WAIT_SEC = 8  # 重启后等进程起来的时间


def wd_log(msg):
    """追加一行心跳/动作日志，超 200 行滚动保留后 100 行。"""
    line = "[%s] %s" % (datetime.now().strftime("%m-%d %H:%M:%S"), msg)
    try:
        lines = []
        if os.path.exists(WD_LOG):
            with open(WD_LOG, encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        lines.append(line)
        if len(lines) > 200:
            lines = lines[-100:]
        with open(WD_LOG, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/pg_robot_watchdog.py", exc)


def robot_pids():
    """存活检测：CommandLine 含 p_g_robot 的 python 进程 PID 列表。"""
    cmd = (
        "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" "
        "| Where-Object {$_.CommandLine -match 'p_g_robot'} "
        "| Select-Object -ExpandProperty ProcessId"
    )
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", cmd],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return [int(x) for x in (r.stdout or "").split() if x.isdigit()]
    except (OSError, subprocess.SubprocessError):
        return []


def read_tail(path, n=80):
    """读日志尾 n 行（UTF-8 容错）。"""
    try:
        with open(path, "rb") as f:
            data = f.read()
        return data.decode("utf-8", errors="replace").splitlines()[-n:]
    except OSError:
        return []


def parse_hms(line):
    """解析日志行首 [HH:MM:SS] → 今天的 datetime；跨零点自洽处理。"""
    m = re.match(r"\[(\d{2}):(\d{2}):(\d{2})\]", line)
    if not m:
        return None
    now = datetime.now()
    t = now.replace(
        hour=int(m.group(1)),
        minute=int(m.group(2)),
        second=int(m.group(3)),
        microsecond=0,
    )
    if t > now + timedelta(minutes=1):  # 日志是昨天的（跨零点）
        t -= timedelta(days=1)
    return t


def ws_stuck(tail):
    """卡死判定：最后一次 disconnected/reconnecting 在最后一次 connected
    之后，且距现在超过 STUCK_MIN 分钟。返回 (是否卡死, 证据行)。"""
    last_conn = -1
    last_rec = -1
    for i, line in enumerate(tail):
        if "feishu-websocket: connected" in line:
            last_conn = i
        elif (
            "feishu-websocket: disconnected" in line
            or "feishu-websocket: reconnecting" in line
        ):
            last_rec = i
    if last_rec <= last_conn:
        return False, None
    ev = tail[last_rec]
    ts = parse_hms(ev)
    if ts is None:
        return False, None
    age = (datetime.now() - ts).total_seconds()
    return age > STUCK_MIN * 60, ev.strip()


def restart_robot():
    """schtasks 重启（ASCII 安全），返回 (是否成功, 备注)。"""
    subprocess.run(
        ["schtasks", "/End", "/TN", TASK_NAME], capture_output=True, timeout=30
    )  # 未运行时报错无妨
    r = subprocess.run(
        ["schtasks", "/Run", "/TN", TASK_NAME], capture_output=True, timeout=30
    )
    ok = r.returncode == 0
    if ok:
        time.sleep(VERIFY_WAIT_SEC)
        ok = bool(robot_pids())
    return ok, ("pids=%s" % robot_pids())


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(st):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/pg_robot_watchdog.py", exc)


def send_alert(key, text):
    """飞书私聊告警（同因冷却 30 分钟）。返回 True=已发出。"""
    st = load_state()
    now = time.time()
    if now - st.get(key, 0) < ALERT_COOLDOWN_SEC:
        wd_log("alert suppressed (cooldown): %s" % key)
        return False
    try:
        with open(ALERT_FILE, "w", encoding="utf-8") as f:
            f.write(text)
        r = subprocess.run(
            [sys.executable, SEND_FEISHU, ALERT_FILE],
            capture_output=True,
            text=True,
            timeout=60,
        )
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        wd_log("alert %s -> %s" % (key, out.splitlines()[-1] if out else "?"))
        sent = "SEND_OK" in (r.stdout or "")
    except (OSError, subprocess.SubprocessError) as e:
        wd_log("alert %s EXC %s" % (key, e))
        sent = False
    if sent:
        st[key] = now
        save_state(st)
    return sent


def selftest():
    """离线用例：解析器与卡死判定（不动生产）。"""
    now = datetime.now().strftime("%H:%M:%S")
    old = "00:01:00"
    cases = [
        # (tail, 期望卡死)
        ([f"[{now}] [stderr] [source] feishu-websocket: connected"], False),
        (
            [
                f"[{old}] [stderr] [source] feishu-websocket: disconnected",
                f"[{old}] [stderr] [source] feishu-websocket: reconnecting (attempt 1)",
            ],
            True,
        ),
        (
            [
                f"[{old}] [stderr] [source] feishu-websocket: disconnected",
                f"[{old}] [stderr] [source] feishu-websocket: reconnecting (attempt 1)",
                f"[{now}] [stderr] [source] feishu-websocket: connected",
            ],
            False,
        ),
        (
            [
                f"[{now}] [stderr] [source] feishu-websocket: disconnected",
                f"[{now}] [stderr] [source] feishu-websocket: reconnecting (attempt 1)",
            ],
            False,
        ),  # 刚断未超时
        ([], False),
    ]
    fail = 0
    for i, (tail, want) in enumerate(cases):
        got, _ = ws_stuck(tail)
        mark = "PASS" if got == want else "FAIL"
        if got != want:
            fail += 1
        print("case%d %s got=%s want=%s" % (i + 1, mark, got, want))
    print("SELFTEST %s" % ("PASS" if fail == 0 else "FAIL(%d)" % fail))
    return 0 if fail == 0 else 1


def main():
    if "--selftest" in sys.argv:
        return selftest()
    pids = robot_pids()
    if not pids:
        ok, note = restart_robot()
        wd_log("DEAD -> restart %s (%s)" % ("OK" if ok else "FAIL", note))
        send_alert(
            "dead",
            "PG-Robot 看门狗：进程消失，已自动重启(%s) %s"
            % ("成功" if ok else "失败，请人工介入", note),
        )
        return 0 if ok else 1
    stuck, ev = ws_stuck(read_tail(ROBOT_LOG))
    if stuck:
        ok, note = restart_robot()
        wd_log("STUCK [%s] -> restart %s (%s)" % (ev, "OK" if ok else "FAIL", note))
        send_alert(
            "stuck",
            "PG-Robot 看门狗：WS 重连卡死超 %d 分钟(%s)，已自动重启(%s)"
            % (STUCK_MIN, ev, "成功" if ok else "失败，请人工介入"),
        )
        return 0 if ok else 1
    wd_log("healthy pids=%s" % pids)
    return 0


if __name__ == "__main__":
    sys.exit(main())
