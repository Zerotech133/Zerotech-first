# -*- coding: utf-8 -*-
"""推送活性巡检（K-STOCK-304 B 件）

由计划任务 Push-Liveness 每小时拉起，run-and-exit：
  核心推送链该到未到 → 飞书私聊告警（同链同日只告一次）。
  检查口径 = 实际产出签名（日志内容/mtime），非任务 rc=0
  （K-STOCK-098 只兜 rc≠0，本件补"rc=0 但内容未达"盲区）。

链清单（LIVE_FEEDS）：
  alert_morning 07:10 预警晨报  → alert_push_task.log 当日 starting morning+exit=0
  ai_morning    08:00 AI晨报    → news ai.log 当日 mtime
  subpush_eod   15:30 订阅EOD   → subscribe_task.out 当日 mtime（行内无日期戳）
  alert_eod     16:05 预警日报  → alert_push_task.log 当日 starting eod+exit=0

自测：python push_liveness.py --selftest（离线用例，不动生产）
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

WD_LOG = r"C:\Temp\push_liveness.log"
STATE_FILE = r"C:\Temp\push_liveness_state.json"
ALERT_FILE = r"C:\Temp\push_liveness_alert.txt"
# K-PUSH-001 P0：换机路径残留（旧机 E 盘）→ __file__ 动态推导
SEND_FEISHU = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "news_collector", "send_feishu.py")
GRACE_MIN = 45          # 时点后宽限（分钟）：过宽限仍无签名才告警

ALERT_TASK_LOG = r"C:\Temp\alert_push_task.log"
NEWS_AI_LOG = r"C:\aios\news_collector\logs\ai.log"
SUB_LOG = r"C:\aios\quant_backtest\logs\subscribe_task.out"

TODAY = datetime.now().strftime("%Y-%m-%d")


def wd_log(msg):
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
    except OSError:
        pass


def _sig_ok(path, patterns):
    """日志尾部当日签名：全部 pattern 命中且含今日日期。"""
    try:
        with open(path, "rb") as f:
            data = f.read()
        text = data.decode("utf-8", errors="replace")
    except OSError:
        return False
    if TODAY not in text:
        return False
    return all(re.search(p, text) for p in patterns)


def _mtime_today_after(path, hh, mm):
    """文件 mtime 为今日且不早于 (hh:mm)（行内无日期戳的链用）。"""
    try:
        mtime = datetime.fromtimestamp(os.path.getmtime(path))
    except OSError:
        return False
    if mtime.strftime("%Y-%m-%d") != TODAY:
        return False
    return (mtime.hour, mtime.minute) >= (hh, mm)


LIVE_FEEDS = [
    # key, 名称, 时点(hh,mm), 检查函数
    ("alert_morning", "预警晨报(07:10)", (7, 10),
     lambda: _sig_ok(ALERT_TASK_LOG, [r"starting morning " + TODAY,
                                      r"exit=0 " + TODAY])),
    ("ai_morning", "AI晨报(08:00)", (8, 0),
     lambda: _mtime_today_after(NEWS_AI_LOG, 8, 0)),
    ("subpush_eod", "订阅EOD推送(15:30)", (15, 30),
     lambda: _mtime_today_after(SUB_LOG, 15, 30)),
    ("alert_eod", "预警日报(16:05)", (16, 5),
     lambda: _sig_ok(ALERT_TASK_LOG, [r"starting eod " + TODAY,
                                      r"exit=0 " + TODAY])),
]


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
    except OSError:
        pass


def send_alert(key, text):
    """飞书私聊告警；同链同日只发一次（state 记 key=date）。"""
    st = load_state()
    tag = "%s@%s" % (key, TODAY)
    if st.get(tag):
        return False
    try:
        with open(ALERT_FILE, "w", encoding="utf-8") as f:
            f.write(text)
        r = subprocess.run([sys.executable, SEND_FEISHU, ALERT_FILE],
                           capture_output=True, text=True, timeout=60)
        sent = "SEND_OK" in (r.stdout or "")
        wd_log("alert %s -> %s" % (key,
                                   ((r.stdout or "").strip().splitlines()
                                    or ["?"])[-1]))
    except (OSError, subprocess.SubprocessError) as e:
        wd_log("alert %s EXC %s" % (key, e))
        sent = False
    if sent:
        st[tag] = datetime.now().strftime("%H:%M")
        save_state(st)
    return sent


def check(now=None):
    """返回 [(key, 名称, ok)]；只检查已过宽限期的链。"""
    now = now or datetime.now()
    out = []
    for key, name, (hh, mm), fn in LIVE_FEEDS:
        due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        grace_end = due.timestamp() + GRACE_MIN * 60
        if now.timestamp() < grace_end:
            continue                      # 未到检查窗口
        out.append((key, name, bool(fn())))
    return out


def selftest():
    """离线用例：签名解析与窗口判断（不动生产）。"""
    import tempfile
    fails = 0
    # 用例1：当日 eod 双签名齐 → ok
    with tempfile.NamedTemporaryFile("w", suffix=".log",
                                     delete=False, encoding="utf-8") as f:
        f.write("starting morning %sT07:10:02\nexit=0 %sT07:10:06\n"
                "starting eod %sT16:05:02\nexit=0 %sT16:05:09\n"
                % (TODAY, TODAY, TODAY, TODAY))
        p1 = f.name
    if not _sig_ok(p1, [r"starting eod " + TODAY, r"exit=0 " + TODAY]):
        fails += 1
        print("case1 FAIL（当日签名应命中）")
    os.unlink(p1)
    # 用例2：昨日签名 → 不算
    yd = datetime.now().strftime("%Y-%m-") + "%02d" % (datetime.now().day - 1 or 1)
    with tempfile.NamedTemporaryFile("w", suffix=".log",
                                     delete=False, encoding="utf-8") as f:
        f.write("starting eod %sT16:05:02\nexit=0 %sT16:05:09\n" % (yd, yd))
        p2 = f.name
    if _sig_ok(p2, [r"starting eod " + TODAY]):
        fails += 1
        print("case2 FAIL（昨日签名不应命中）")
    os.unlink(p2)
    # 用例3：宽限窗口——时点刚过 10 分钟不应进入检查
    fake_now = datetime.now().replace(hour=16, minute=15)
    keys = [k for k, _, _ in check(fake_now)]
    if "alert_eod" in keys:
        fails += 1
        print("case3 FAIL（宽限期内不应检查）")
    # 用例4：时点过 1 小时应进入检查
    fake_now = datetime.now().replace(hour=17, minute=5)
    keys = [k for k, _, _ in check(fake_now)]
    if "alert_eod" not in keys:
        fails += 1
        print("case4 FAIL（宽限期后应检查）")
    print("SELFTEST %s" % ("PASS" if fails == 0 else "FAIL(%d)" % fails))
    return 0 if fails == 0 else 1


def main():
    if "--selftest" in sys.argv:
        return selftest()
    results = check()
    if not results:
        wd_log("no feed due yet")
        return 0
    bad = [(k, n) for k, n, ok in results if not ok]
    good = [k for k, _, ok in results if ok]
    if good:
        wd_log("ok: %s" % ",".join(good))
    for key, name in bad:
        wd_log("MISSING %s" % key)
        send_alert(key,
                   "推送巡检：%s 该到未到（已过 %d 分钟宽限）。"
                   "请查对应任务日志；本告警当日不重复。"
                   % (name, GRACE_MIN))
    return 0


if __name__ == "__main__":
    sys.exit(main())
