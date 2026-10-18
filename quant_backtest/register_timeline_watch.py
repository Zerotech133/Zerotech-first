#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""注册每日计划任务 XueqiuTimelineWatch（pywin32 COM，2026-10-18）。

宿主拦截 Register-ScheduledTask 与 schtasks.exe，故全程 pywin32 COM：
  TaskScheduler COM（Schedule.Service）+ InteractiveToken + LUA（当前用户）。
只新增/覆盖自己的 XueqiuTimelineWatch，绝不枚举/改动任何既有任务
（XueqiuCrawl/XueqiuDailyReport/XueqiuReportVerify/XueqiuShadowDaily 等）。

触发：每日 19:35（CalendarTrigger/ScheduleByDay，DaysInterval=1，无限重复）。
动作：powershell.exe -NoProfile -ExecutionPolicy Bypass -File
      <repo>/start_timeline_watch.ps1（全 ASCII wrapper，日志落
      C:/aios/automation_runs/xq_timeline/）。

StartBoundary 取「今天 19:35」（sched-polish-dev 刀一，2026-10-18）：
  - 注册时实际时间 15:17，早于今天 19:35，今天的首跑仍由本任务承担
    （原 OneTime 触发器被本次覆盖，边界取明天会吞掉今晚首跑）；
  - StartWhenAvailable=False，即使注册晚于 19:35 也不会注册即补跑，
    此时 NextRunTime 自动落在明天 19:35，无补跑风险；
  - 今晚跑过之后，终态 NextRunTime=2026-10-19 19:35，每日循环。

注册成功后回读 task.NextRunTime（属性，非方法）+ 触发器锚点
（DaysInterval==1）+ 导出 XML 断言 ScheduleByDay。
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
TASK_TRIGGER_DAILY = 2
TASK_CREATE_OR_UPDATE = 6
TASK_LOGON_INTERACTIVE_TOKEN = 3
TASK_RUNLEVEL_LUA = 0


def _tomorrow(day_str: str) -> str:
    from datetime import timedelta
    d = datetime.strptime(day_str, "%Y-%m-%d") + timedelta(days=1)
    return d.strftime("%Y-%m-%d")


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
            "collector, daily 19:35). Owned by watch-timeline-dev.")
        td.RegistrationInfo.Author = "watch-timeline-dev"

        # 每日触发：CalendarTrigger / ScheduleByDay，DaysInterval=1 无限重复。
        # StartBoundary 取今天 19:35（若注册已晚于今天 19:35，配合
        # StartWhenAvailable=False 自动顺延到明天，绝不补跑）。
        today = datetime.now().strftime("%Y-%m-%d")
        trig = td.Triggers.Create(TASK_TRIGGER_DAILY)
        trig.Id = "TimelineWatchDailyTrigger"
        trig.StartBoundary = "%sT19:35:00" % today
        trig.DaysInterval = 1
        trig.Enabled = True

        act = td.Actions.Create(TASK_ACTION_EXEC)
        act.Id = "TimelineWatchAction"
        act.Path = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
        act.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "%s"' % WRAPPER
        act.WorkingDirectory = REPO

        # InteractiveToken + LUA（非提权），当前用户；错过触发点不补跑
        # （StartWhenAvailable=False 保守语义：过期边界只会顺延到下一日
        # 19:35，绝不注册即补跑）
        td.Principal.LogonType = TASK_LOGON_INTERACTIVE_TOKEN
        td.Principal.RunLevel = TASK_RUNLEVEL_LUA
        td.Settings.Enabled = True
        td.Settings.StartWhenAvailable = False
        td.Settings.MultipleInstances = 0  # 不并行
        td.Settings.ExecutionTimeLimit = "PT30M"  # 收集器秒级完成，30 分钟上限兜底

        root.RegisterTaskDefinition(TASK_NAME, td, TASK_CREATE_OR_UPDATE,
                                    None, None, TASK_LOGON_INTERACTIVE_TOKEN)
        print("[register] task %s registered (Daily 19:35, DaysInterval=1, "
              "StartBoundary=%sT19:35:00)" % (TASK_NAME, today))

        # 回读验证 1：NextRunTime 是属性不是方法
        task = root.GetTask(TASK_NAME)
        nrt = task.NextRunTime
        state = task.State
        print("[register] NextRunTime=%s State=%s" % (nrt, state))
        # NextRunTime 要么今天 19:35（注册早于今天 19:35，今晚首跑），
        # 要么明天 19:35（注册晚于今天 19:35，顺延无补跑）
        ok_nrt = ("19:35" in str(nrt)
                  and (str(nrt).startswith(today)
                       or str(nrt).startswith(_tomorrow(today))))

        # 回读验证 2：触发器锚点——唯一触发器、日历日触发、DaysInterval==1
        trig0 = task.Definition.Triggers.Item(1)  # COM 集合 1 起始
        n_trig = task.Definition.Triggers.Count
        print("[register] triggers=%d type=%s DaysInterval=%s StartBoundary=%s"
              % (n_trig, trig0.Type, getattr(trig0, "DaysInterval", "?"),
                 trig0.StartBoundary))
        ok_trig = (n_trig == 1 and trig0.Type == TASK_TRIGGER_DAILY
                   and getattr(trig0, "DaysInterval", 0) == 1
                   and "T19:35:00" in str(trig0.StartBoundary))

        # 回读验证 3：导出 XML 断言 ScheduleByDay/DaysInterval
        xml = task.Xml
        ok_xml = ("<ScheduleByDay>" in xml
                  and "<DaysInterval>1</DaysInterval>" in xml)
        print("[register] XML ScheduleByDay check: %s"
              % ("OK" if ok_xml else "MISMATCH"))

        ok = ok_nrt and ok_trig and ok_xml
        print("[register] final check: %s (nrt=%s trig=%s xml=%s)"
              % ("OK" if ok else "MISMATCH",
                 "OK" if ok_nrt else "MISMATCH",
                 "OK" if ok_trig else "MISMATCH",
                 "OK" if ok_xml else "MISMATCH"))
        return 0 if ok else 2
    except COMError as e:
        print("[register] COMError: %r" % (e,))
        return 1
    finally:
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    sys.exit(main())
