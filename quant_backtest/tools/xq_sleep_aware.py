# -*- coding: utf-8 -*-
"""睡眠感知判活工具（xq 情报线 O1，2026-10-19）。

背景判例：2026-10-19 01:08:42 系统睡眠 / 03:36:35 唤醒
（Kernel-Power Id=42 睡眠、Power-Troubleshooter Id=1 唤醒，
事件消息内 UTC 为 10-18T17:08Z，对应本地 10-19 01:08）。
睡眠期间进程冻结不死亡、醒来无缝续跑，导致治理线把
「速率骤降 2h28m」误判为限流/故障。长跑/巡检的判活与 ETA
都应剔除睡眠窗。

实机探针结论（见 C:/aios/automation_runs/xq_o1_sleepaware/probe_events.txt）：
  - Microsoft-Windows-Kernel-Power Id=42   ：睡眠开始，TimeCreated = 睡眠起点
  - Microsoft-Windows-Power-Troubleshooter Id=1：唤醒，TimeCreated ≈ 唤醒时刻（约 +3s），
    消息内含「睡眠时间/唤醒时间」（UTC，格式 2026-..T..Z）
  - Kernel-Power Id=107 在本机（Modern Standby）睡眠开始后 12~26s 即触发，
    不是真正唤醒点，故不用它做配对闭合；唤醒事件缺失时以当前时间兜底闭合。

Windows-only：事件日志查询仅在 Windows 上可用；非 Windows 平台
sleep_windows() 返回空列表（不报错），effective_runtime 退化为墙钟时长，
is_frozen_alive 退化为纯 mtime 新鲜度两态（ALIVE/DEAD，无 STALLED_SLEEP）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta

IS_WINDOWS = sys.platform == "win32"  # 模块级变量，便于测试在任意平台注入

_SLEEP_ID = 42  # Microsoft-Windows-Kernel-Power：进入睡眠
_WAKE_ID = 1  # Microsoft-Windows-Power-Troubleshooter：从低功耗状态恢复

_EVENT_LINE_RE = re.compile(r"^(42|1)\|(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})$")

# 纯 ASCII 输出（仅 事件ID|时间戳），与控制台代码页无关，规避 GBK/UTF-8 乱码
# （判例：elevated_kill v1 中文事件消息乱码教训——这里根本不取消息文本）。
_PS_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$since = (Get-Date).AddMinutes(-{minutes:.0f}).ToString('yyyy-MM-ddTHH:mm:ss')
Get-WinEvent -FilterHashtable @{{ LogName='System'; ProviderName='Microsoft-Windows-Kernel-Power'; Id=42; StartTime=$since }} |
  ForEach-Object {{ '42|' + $_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss') }}
Get-WinEvent -FilterHashtable @{{ LogName='System'; ProviderName='Microsoft-Windows-Power-Troubleshooter'; Id=1; StartTime=$since }} |
  ForEach-Object {{ '1|' + $_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss') }}
"""

SleepWindow = tuple  # (start_dt, end_dt)，本地 naive datetime


def fetch_power_events(hours: float) -> list:
    """从 System 事件日志拉取睡眠/唤醒事件，返回 [(event_id, dt), ...]（按时间升序）。

    仅 Windows 可用；失败（无事件/权限/超时）返回空列表，不抛异常。
    """
    if not IS_WINDOWS:
        return []
    script = _PS_SCRIPT.format(minutes=max(1.0, float(hours) * 60.0))
    try:
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    events = []
    for line in (proc.stdout or "").splitlines():
        m = _EVENT_LINE_RE.match(line.strip())
        if m:
            events.append(
                (int(m.group(1)), datetime.strptime(m.group(2), "%Y-%m-%d %H:%M:%S"))
            )
    events.sort(key=lambda e: e[1])
    return events


def pair_sleep_windows(events, now=None):
    """把事件流配对成睡眠区间 [(start, end), ...]。

    规则：Id=42 开窗，之后第一个 Id=1 闭窗；未闭合的窗以 now 兜底闭合
    （睡眠中查询时，进程仍在冻结，以当前时间闭合即"睡到现在"）。
    无主的 Id=1（前面没有 42）忽略。
    """
    if now is None:
        now = datetime.now()
    windows = []
    start = None
    for eid, dt in sorted(events, key=lambda e: e[1]):
        if eid == _SLEEP_ID:
            if start is None:
                start = dt
        elif eid == _WAKE_ID:
            if start is not None:
                if dt > start:
                    windows.append((start, dt))
                start = None
    if start is not None:  # 兜底：睡眠事件未见唤醒 → 以当前时间闭合
        end = now if now > start else start
        windows.append((start, end))
    return windows


def sleep_windows(hours=24.0, events=None, now=None):
    """返回最近 hours 小时内的睡眠区间列表 [(start_dt, end_dt), ...]。

    events 参数用于测试注入假事件列表；为 None 时实查系统事件日志。
    非 Windows 平台返回 []（Windows-only 设计，见模块 docstring）。
    """
    if not IS_WINDOWS:
        return []
    if now is None:
        now = datetime.now()
    if events is None:
        events = fetch_power_events(hours)
    windows = pair_sleep_windows(events, now=now)
    since = now - timedelta(hours=float(hours))
    return [w for w in windows if w[1] > since and w[0] < now]


def _overlap(a0, a1, b0, b1):
    lo, hi = max(a0, b0), min(a1, b1)
    return (hi - lo) if hi > lo else timedelta(0)


def effective_runtime(start_dt, end_dt, windows=None):
    """剔除睡眠窗后的净运行时长（timedelta）。

    windows 为 None 时按 [start_dt, end_dt] 跨度自动取睡眠窗（多留 1h 余量）。
    """
    if end_dt <= start_dt:
        return timedelta(0)
    if windows is None:
        hours = (end_dt - start_dt).total_seconds() / 3600.0 + 1.0
        windows = sleep_windows(hours)
    sleep_total = sum(
        (_overlap(w[0], w[1], start_dt, end_dt) for w in windows), timedelta(0)
    )
    net = (end_dt - start_dt) - sleep_total
    return net if net > timedelta(0) else timedelta(0)


ALIVE = "ALIVE"
STALLED_SLEEP = "STALLED_SLEEP"
DEAD = "DEAD"


def is_frozen_alive(mtime_path, max_idle_min=20, windows=None, now=None):
    """文件新鲜度判活（三态），供巡检脚本替换裸 mtime 判断。

    - mtime 在 max_idle_min 内                                      → ALIVE
    - 裸 idle 超限，但 (mtime, now] 内的睡眠窗可解释全部超时部分
      （即扣除睡眠后净 idle ≤ max_idle_min）                         → STALLED_SLEEP（疑似睡眠停滞）
    - 扣除睡眠后仍超限                                              → DEAD
    文件不存在返回 DEAD。mtime 恰落在睡眠窗内时，睡眠自然覆盖其后段，
    净 idle 变小，同样落入 STALLED_SLEEP 而非误判死亡。
    """
    if now is None:
        now = datetime.now()
    if not os.path.exists(mtime_path):
        return DEAD
    mtime = datetime.fromtimestamp(os.path.getmtime(mtime_path))
    idle = now - mtime
    if idle <= timedelta(0):  # 时钟偏移/未来时间戳，视为活跃
        return ALIVE
    if windows is None:
        windows = sleep_windows(hours=(idle.total_seconds() / 3600.0 + 1.0), now=now)
    sleep_total = sum((_overlap(w[0], w[1], mtime, now) for w in windows), timedelta(0))
    effective_idle = idle - sleep_total
    if effective_idle <= timedelta(minutes=max_idle_min):
        return STALLED_SLEEP if sleep_total > timedelta(0) else ALIVE
    return DEAD


def _fmt_td(td):
    s = int(td.total_seconds())
    return "{:d}:{:02d}:{:02d}".format(s // 3600, (s % 3600) // 60, s % 60)


def build_report(hours, windows, now):
    net = effective_runtime(now - timedelta(hours=hours), now, windows=windows)
    total_sleep = sum((w[1] - w[0] for w in windows), timedelta(0))
    return {
        "platform": "windows" if IS_WINDOWS else "non-windows",
        "hours": hours,
        "now": now.strftime("%Y-%m-%d %H:%M:%S"),
        "sleep_windows": [
            {
                "start": w[0].strftime("%Y-%m-%d %H:%M:%S"),
                "end": w[1].strftime("%Y-%m-%d %H:%M:%S"),
                "duration_s": int((w[1] - w[0]).total_seconds()),
            }
            for w in windows
        ],
        "total_sleep_s": int(total_sleep.total_seconds()),
        "effective_runtime_s": int(net.total_seconds()),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="睡眠感知判活工具（判活/ETA 应剔除系统睡眠窗）"
    )
    ap.add_argument(
        "--report",
        nargs="?",
        type=float,
        const=24.0,
        default=None,
        metavar="HOURS",
        help="打印最近 HOURS 小时的人类可读报告（默认 24）",
    )
    ap.add_argument(
        "--json", action="store_true", help="输出机器可读 JSON（配合 --report 的时段）"
    )
    args = ap.parse_args(argv)
    if args.report is None and not args.json:
        ap.print_help()
        return 1
    hours = args.report if args.report is not None else 24.0
    now = datetime.now()
    windows = sleep_windows(hours, now=now)
    rep = build_report(hours, windows, now)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0
    print("睡眠感知判活报告（最近 {} 小时）".format(rep["hours"]))
    print(
        "平台: {} | 事件源: System 日志 (Kernel-Power Id=42 / Power-Troubleshooter Id=1)".format(
            rep["platform"]
        )
    )
    if not windows:
        print("睡眠窗: 无（系统事件日志未探到 Id=42 睡眠事件，或非 Windows 平台）")
    else:
        print(
            "睡眠窗 {} 个, 合计 {}:".format(
                len(windows), _fmt_td(timedelta(seconds=rep["total_sleep_s"]))
            )
        )
        for i, w in enumerate(rep["sleep_windows"], 1):
            print(
                "  [{}] {} -> {}  ({})".format(
                    i, w["start"], w["end"], _fmt_td(timedelta(seconds=w["duration_s"]))
                )
            )
    print(
        "净运行时长示例: 最近 {}h 墙钟 {}h，剔除睡眠后净 {}（{}）".format(
            rep["hours"],
            _fmt_td(timedelta(hours=rep["hours"])),
            _fmt_td(timedelta(seconds=rep["effective_runtime_s"])),
            "睡眠感知" if windows else "无睡眠窗, 等于墙钟",
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
