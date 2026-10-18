#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""注册一次性计划任务 XueqiuTimelineWatch（pywin32 COM，2026-10-18）。

宿主拦截 Register-ScheduledTask 与 schtasks.exe，故全程 pywin32 COM：
  TaskScheduler COM（Schedule.Service）+ InteractiveToken + LUA（当前用户）。
只新增/覆盖自己的 XueqiuTimelineWatch，绝不枚举/改动任何既有任务
（XueqiuCrawl/XueqiuDailyReport/XueqiuReportVerify/XueqiuShadowDaily 等）。

触发：今天 19:35 一次性（OneTime，无重复间隔）。
动作：powershell.exe -NoProfile -ExecutionPolicy Bypass -File
      <repo>/start_timeline_watch.ps1（全 ASCII wrapper，日志落
      C:/aios/automation_runs/xq_timeline/）。

注册成功后回读 task.NextRunTime（属性，非方法）确认今天 19:35。
"""
import sys
from datetime import datetime

import pythoncom
import win32com.client
from pythoncom import com_error as COMError

TASK_NAME = "XueqiuTimelineWatch"
REPO = r"C:\项目\老田的AI助手\quant_backtest"
WRAPPER = REPO + r"\start_timeline_watch.ps1"

# TaskScheduler COM 常量（win32com.client.constants 不可靠，直接用文档值）
TASK_ACTION_EXEC = 0
TASK_TRIGGER_TIME = 1
TASK_CREATE_OR_UPDATE = 6
TASK_LOGON_INTERACTIVE_TOKEN = 3
TASK_RUNLEVEL_LUA = 0


def main() -> int:
    pythoncom.CoInitialize()
    try:
        sched = win32com.client.Dispatch("Schedule.Service")
        sched.Connect()  # 当前用户、本机
        root = sched.GetFolder("\\")

        # 红线自检：只许碰自己的任务名。若同名任务存在（上次注册残留），
        # TASK_CREATE_OR_UPDATE 覆盖的仍是自己的任务，不触碰其他任务。
        # 注意：不做任何 GetTask(其他名) / Stop / Delete / 触发器修改。

        td = sched.NewTask(0)
        td.RegistrationInfo.Description = (
            "Xueqiu production-chain timeline watch (read-only evidence "
            "collector, one-shot 19:35). Owned by watch-timeline-dev.")
        td.RegistrationInfo.Author = "watch-timeline-dev"

        # 一次性触发：今天 19:35 本地时间，无重复
        today = datetime.now().strftime("%Y-%m-%d")
        trig = td.Triggers.Create(TASK_TRIGGER_TIME)
        trig.Id = "TimelineWatchOnceTrigger"
        trig.StartBoundary = "%sT19:35:00" % today
        trig.Enabled = True

        act = td.Actions.Create(TASK_ACTION_EXEC)
        act.Id = "TimelineWatchAction"
        act.Path = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
        act.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "%s"' % WRAPPER
        act.WorkingDirectory = REPO

        # InteractiveToken + LUA（非提权），当前用户；错过触发点不补跑
        # （19:35 过后补跑无意义，留 StartWhenAvailable=False 保守语义）
        td.Principal.LogonType = TASK_LOGON_INTERACTIVE_TOKEN
        td.Principal.RunLevel = TASK_RUNLEVEL_LUA
        td.Settings.Enabled = True
        td.Settings.StartWhenAvailable = False
        td.Settings.MultipleInstances = 0  # 不并行
        td.Settings.ExecutionTimeLimit = "PT30M"  # 收集器秒级完成，30 分钟上限兜底

        root.RegisterTaskDefinition(TASK_NAME, td, TASK_CREATE_OR_UPDATE,
                                    None, None, TASK_LOGON_INTERACTIVE_TOKEN)
        print("[register] task %s registered (OneTime %sT19:35:00)"
              % (TASK_NAME, today))

        # 回读验证：NextRunTime 是属性不是方法
        task = root.GetTask(TASK_NAME)
        nrt = task.NextRunTime
        state = task.State
        print("[register] NextRunTime=%s State=%s" % (nrt, state))
        expect = "%s 19:35:00" % today
        ok = str(nrt).startswith(today) and "19:35" in str(nrt)
        print("[register] NextRunTime check: %s (expect %s)"
              % ("OK" if ok else "MISMATCH", expect))
        return 0 if ok else 2
    except COMError as e:
        print("[register] COMError: %r" % (e,))
        return 1
    finally:
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    sys.exit(main())
