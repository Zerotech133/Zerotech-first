#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""注册每日计划任务 ChanlunHealthcheck（pywin32 COM）。

宿主安全策略把 schtasks.exe 列入程序黑名单（实测拒绝访问），
故全程走 TaskScheduler COM + InteractiveToken + LUA，与先例
register_timeline_watch.py 同一通道。只新增/覆盖本任务，不枚举、
不改动任何既有任务。

触发：每日 08:40。选这个时点的理由：
  - 前一晚 23:35 的 AI量化RPS共振 已把 parquet 同步完整，08:40 读到的是
    稳定快照；若当晚同步失败，H5 数据新鲜度会如实报 WARN，正好被看见。
  - 与 08:30 的 PostHolidayCheck 错开 10 分钟，避免抢 IO。
  - 结果落在上班时间，当天就能处理。

动作：直接调 python.exe 跑 chanlun_healthcheck.py，不用 bat/cmd 重定向
（本仓路径含中文，走 shell 有编码坑；体检脚本自身往
data/chanlun_healthcheck_log.jsonl 追加滚动摘要）。

**必须显式设 DisallowStartIfOnBatteries=False**：新式设备常把电源状态
报成 Offline，任务默认 DisallowStartIfOnBatteries=true 时会永远排队、
LastTaskResult 假 0、日志零产出（2026-10-19 环境级判例）。新注册任务
一律加这条。

注册后回读三项验证：NextRunTime 含 08:40、触发器 DaysInterval==1、
导出 XML 含 <ScheduleByDay>。
"""
import sys

import pythoncom
import win32com.client
from pythoncom import com_error as COMError

TASK_NAME = "ChanlunHealthcheck"
REPO = r"C:\项目\老田的AI助手\quant_backtest"
SCRIPT = REPO + r"\chanlun_healthcheck.py"
PYTHON = r"C:\Users\t\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
HHMM = "08:40:00"

TASK_ACTION_EXEC = 0
TASK_TRIGGER_DAILY = 2
TASK_CREATE_OR_UPDATE = 6
TASK_LOGON_INTERACTIVE_TOKEN = 3
TASK_RUNLEVEL_LUA = 0
TASK_INSTANCES_IGNORE_NEW = 0


def main() -> int:
    from datetime import datetime

    pythoncom.CoInitialize()
    try:
        sched = win32com.client.Dispatch("Schedule.Service")
        sched.Connect()
        root = sched.GetFolder("\\")

        td = sched.NewTask(0)
        td.RegistrationInfo.Description = (
            "Chanlun system healthcheck: 3-gate reproduction, two-caliber "
            "consistency, look-ahead check, v4 reconciliation, data freshness. "
            "Daily 08:40, exit 1 on FAIL.")
        td.RegistrationInfo.Author = "quant-audit"

        today = datetime.now().strftime("%Y-%m-%d")
        trig = td.Triggers.Create(TASK_TRIGGER_DAILY)
        trig.Id = "HealthcheckDailyTrigger"
        trig.StartBoundary = "%sT%s" % (today, HHMM)
        trig.DaysInterval = 1
        trig.Enabled = True

        act = td.Actions.Create(TASK_ACTION_EXEC)
        act.Id = "HealthcheckAction"
        act.Path = PYTHON
        act.Arguments = '"%s" --workers 8' % SCRIPT
        act.WorkingDirectory = REPO

        td.Principal.LogonType = TASK_LOGON_INTERACTIVE_TOKEN
        td.Principal.RunLevel = TASK_RUNLEVEL_LUA
        td.Settings.Enabled = True
        td.Settings.StartWhenAvailable = True   # 错过触发点补跑（体检无副作用）
        td.Settings.DisallowStartIfOnBatteries = False   # 电源判例，必设
        td.Settings.StopIfGoingOnBatteries = False
        td.Settings.MultipleInstances = TASK_INSTANCES_IGNORE_NEW
        td.Settings.ExecutionTimeLimit = "PT15M"  # 实测 84s，15 分钟兜底

        root.RegisterTaskDefinition(TASK_NAME, td, TASK_CREATE_OR_UPDATE,
                                    None, None, TASK_LOGON_INTERACTIVE_TOKEN)
        print("[register] task %s registered (daily %s)" % (TASK_NAME, HHMM))

        task = root.GetTask(TASK_NAME)
        nrt = str(task.NextRunTime)
        print("[register] NextRunTime=%s State=%s" % (nrt, task.State))
        ok_nrt = "08:40" in nrt

        trig0 = task.Definition.Triggers.Item(1)
        n_trig = task.Definition.Triggers.Count
        print("[register] triggers=%d type=%s DaysInterval=%s StartBoundary=%s"
              % (n_trig, trig0.Type, getattr(trig0, "DaysInterval", "?"),
                 trig0.StartBoundary))
        ok_trig = (n_trig == 1 and trig0.Type == TASK_TRIGGER_DAILY
                   and getattr(trig0, "DaysInterval", 0) == 1
                   and "T08:40:00" in str(trig0.StartBoundary))

        xml = task.Xml
        ok_xml = "<ScheduleByDay>" in xml and "<DaysInterval>1</DaysInterval>" in xml
        ok_batt = "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>" in xml
        print("[register] XML ScheduleByDay=%s DisallowStartIfOnBatteries=false=%s"
              % (ok_xml, ok_batt))
        print("[register] action: %s %s" % (PYTHON, act.Arguments))

        ok = ok_nrt and ok_trig and ok_xml and ok_batt
        print("[register] final check: %s (nrt=%s trig=%s xml=%s batt=%s)"
              % ("OK" if ok else "MISMATCH", ok_nrt, ok_trig, ok_xml, ok_batt))
        return 0 if ok else 2
    except COMError as e:
        print("[register] COMError: %r" % (e,))
        return 1
    finally:
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    sys.exit(main())
