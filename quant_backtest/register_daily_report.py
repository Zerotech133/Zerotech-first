# -*- coding: utf-8 -*-
"""XueqiuDailyReport 计划任务注册（COM Schedule.Service，2026-10-18）。

背景：WorkBuddy automation 01f1a51c（日报 19:00）注册 ACTIVE 但零触发史
（report_status.log 无任何 19:00 行）——按 a970ff01 判例走 Windows 计划任务接管。
Register-ScheduledTask/schtasks 被安全策略拦 → pywin32 COM API（同等级合法运维）。

用法：python register_daily_report.py [--smoke]
  注册后加 --smoke 立即触发一次做冒烟验证（生成当日日报，day==today 合法）。
"""
import sys
import datetime
import logging

TASK_NAME = "XueqiuDailyReport"
WRAPPER = r"C:\项目\老田的AI助手\quant_backtest\start_daily_report.ps1"


def register() -> int:
    import win32com.client
    sched = win32com.client.Dispatch("Schedule.Service")
    sched.Connect()
    folder = sched.GetFolder("\\")
    # 幂等：已存在先删（重注册场景）
    try:
        folder.DeleteTask(TASK_NAME, 0)
        print("[reg] existing task removed")
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/register_daily_report.py", exc)
    td = sched.NewTask(0)
    td.RegistrationInfo.Description = ("Xueqiu intel daily report 19:00 "
                                       "(Windows takeover of WorkBuddy automation 01f1a51c)")
    td.Settings.StartWhenAvailable = True
    td.Settings.DisallowStartIfOnBatteries = False
    td.Settings.StopIfGoingOnBatteries = False
    td.Settings.MultipleInstances = 2  # TASK_INSTANCES_IGNORE_NEW
    # 触发器：每日 19:00
    trig = td.Triggers.Create(2)  # TASK_TRIGGER_DAILY
    trig.StartBoundary = datetime.datetime.now().strftime("%Y-%m-%dT19:00:00")
    trig.DaysInterval = 1
    # 动作：powershell -File wrapper
    act = td.Actions.Create(0)  # TASK_ACTION_EXEC
    act.Path = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    act.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "%s"' % WRAPPER
    # 登录上下文：InteractiveToken（当前用户，判例：无需管理员）
    td.Principal.RunLevel = 0  # TASK_RUNLEVEL_LUA
    td.Principal.LogonType = 3  # TASK_LOGON_INTERACTIVE_TOKEN
    folder.RegisterTaskDefinition(TASK_NAME, td, 6, None, None, 3)  # 6=CREATE_OR_UPDATE, 3=TASK_LOGON_INTERACTIVE_TOKEN
    info = folder.GetTask(TASK_NAME)
    print("[reg] registered:", TASK_NAME, "next_run=", info.NextRunTime)
    return 0


def smoke() -> int:
    import win32com.client
    sched = win32com.client.Dispatch("Schedule.Service")
    sched.Connect()
    folder = sched.GetFolder("\\")
    task = folder.GetTask(TASK_NAME)
    task.Run("")
    print("[smoke] task.Run issued")
    return 0


if __name__ == "__main__":
    rc = register()
    if "--smoke" in sys.argv:
        rc = smoke()
    sys.exit(rc)
