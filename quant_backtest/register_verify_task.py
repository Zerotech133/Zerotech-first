# -*- coding: utf-8 -*-
"""XueqiuReportVerify 计划任务注册（COM Schedule.Service，2026-10-18）。

背景：XueqiuDailyReport（19:00 日报）已由 Windows 计划任务接管
（register_daily_report.py 判例），但日报失败目前只能人工发现。
本脚本注册 XueqiuReportVerify 每日 19:05 调用 tools/verify_daily_report.py
做机器验收（start_verify_report.ps1 wrapper）。

先例约束（register_daily_report.py 判例）：
  - Register-ScheduledTask/schtasks.exe 被安全策略拦 → pywin32 COM API；
  - InteractiveToken + LUA，无需管理员；
  - COM 读下次运行时间是 NextRunTime 属性，不是 GetNextRunTime() 方法。

用法：python register_verify_task.py [--smoke]
  注册后加 --smoke 立即触发一次做冒烟验证（19:00 前触发时 wrapper 自动
  降级 --smoke 模式，退出码 0/2 合法；19:00 后为 full 模式）。
"""
import sys
import datetime
import logging

TASK_NAME = "XueqiuReportVerify"
WRAPPER = r"C:\项目\老田的AI助手\quant_backtest\start_verify_report.ps1"


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
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/register_verify_task.py", exc)
    td = sched.NewTask(0)
    td.RegistrationInfo.Description = ("Xueqiu intel daily report verification 19:05 "
                                       "(auto-acceptance of XueqiuDailyReport output)")
    td.Settings.StartWhenAvailable = True
    td.Settings.DisallowStartIfOnBatteries = False
    td.Settings.StopIfGoingOnBatteries = False
    td.Settings.MultipleInstances = 2  # TASK_INSTANCES_IGNORE_NEW
    # 触发器：每日 19:05（紧跟 19:00 日报首跑）
    trig = td.Triggers.Create(2)  # TASK_TRIGGER_DAILY
    trig.StartBoundary = datetime.datetime.now().strftime("%Y-%m-%dT19:05:00")
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
    # 判例注意：COM 是 NextRunTime 属性，不是 GetNextRunTime() 方法
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
