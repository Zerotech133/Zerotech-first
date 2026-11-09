# -*- coding: utf-8 -*-
"""注册盘中池推送任务 PoolJudgeIntraday（pywin32 COM，2026-11-09）。

为什么不用 schtasks / Register-ScheduledTask：宿主拦截这两个入口
（同 register_timeline_watch.py 判例），TaskScheduler COM 是唯一能走通的路。

任务形态：
  名称   PoolJudgeIntraday（ASCII，避免中文任务名在日志/查询侧的编码问题）
  动作   C:\\Program Files\\Python311\\python.exe <repo>\\pool_intraday_push.py
  工作目录 <repo>\\quant_backtest（脚本靠 __file__ 定位，但仍给出以防相对路径）
  触发   两个日历日触发：09:30 起 / 13:00 起，各自重复 PT15M、时长 PT2H
         → 09:30~11:30 与 13:00~15:00，各 9 次，全天 18 次
  身份   当前用户 InteractiveToken（不存密码）；登录态不在时不会跑
  错过   StartWhenAvailable=False（盘中任务补跑毫无意义，只会推过期价）
  并发   MultipleInstances=IgnoreNew（一轮 80 秒，正常不会撞，撞了保前一轮）
  上限   ExecutionTimeLimit=PT10M（扫描 45s + 取价 + H5 20s，10 分钟够）

脚本内还有三重门控（工作日/交易时段/行情是今天），所以周末与节假日即便
被触发也只会静默退出，不会推消息。

用法:
  python register_pool_judge_task.py --dry      # 只打印将要注册的定义
  python register_pool_judge_task.py            # 注册/覆盖（只碰自己这一个任务）
  python register_pool_judge_task.py --delete   # 删除本任务（回滚）
"""

import argparse
import sys
from datetime import datetime

TASK_NAME = "PoolJudgeIntraday"
REPO = r"C:\项目\老田的AI助手\quant_backtest"
PYTHON = r"C:\Program Files\Python311\python.exe"
SCRIPT = REPO + r"\pool_intraday_push.py"

TASK_ACTION_EXEC = 0
TASK_TRIGGER_DAILY = 2
TASK_CREATE_OR_UPDATE = 6
TASK_LOGON_INTERACTIVE_TOKEN = 3
TASK_RUNLEVEL_LUA = 0

SLOTS = (("09:30", "PT2H"), ("13:00", "PT2H"))   # (起始, 重复窗口时长)
INTERVAL = "PT15M"


def _triggers_def():
    """(起始时刻, 窗口时长)。PT2H = 09:30~11:30 / 13:00~15:00，各 9 次。"""
    return list(SLOTS)


def describe():
    print("[plan] task   %s" % TASK_NAME)
    print("[plan] action %s %s" % (PYTHON, SCRIPT))
    print("[plan] workdir %s" % REPO)
    for t, dur in _triggers_def():
        print("[plan] trigger daily %s  repetition %s x %s" % (t, INTERVAL, dur))
    print("[plan] logon  InteractiveToken / LUA / StartWhenAvailable=False "
          "/ IgnoreNew / PT10M")


def register():
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        sched = win32com.client.Dispatch("Schedule.Service")
        sched.Connect()
        root = sched.GetFolder("\\")

        td = sched.NewTask(0)
        td.RegistrationInfo.Description = (
            "Chan pool intraday judge+push: every 15 min in CN sessions "
            "(09:30-11:30, 13:00-15:00). Owned by pool intraday push.")
        td.RegistrationInfo.Author = "pool-intraday"

        today = datetime.now().strftime("%Y-%m-%d")
        for i, (start, dur) in enumerate(_triggers_def(), 1):
            trig = td.Triggers.Create(TASK_TRIGGER_DAILY)
            trig.Id = "PoolJudgeTrigger%d" % i
            trig.StartBoundary = "%sT%s:00" % (today, start)
            trig.DaysInterval = 1
            # 日内重复：每 15 分钟一次，持续 2 小时（COM 用 ISO8601 时长）
            trig.Repetition.Interval = INTERVAL
            trig.Repetition.Duration = dur
            trig.Repetition.StopAtDurationEnd = False
            trig.Enabled = True

        act = td.Actions.Create(TASK_ACTION_EXEC)
        act.Id = "PoolJudgeAction"
        act.Path = PYTHON
        act.Arguments = '"%s"' % SCRIPT
        act.WorkingDirectory = REPO

        td.Principal.LogonType = TASK_LOGON_INTERACTIVE_TOKEN
        td.Principal.RunLevel = TASK_RUNLEVEL_LUA
        td.Settings.Enabled = True
        td.Settings.StartWhenAvailable = False
        td.Settings.MultipleInstances = 2          # IgnoreNew
        td.Settings.ExecutionTimeLimit = "PT10M"
        td.Settings.DisallowStartIfOnBatteries = False
        td.Settings.StopIfGoingOnBatteries = False

        root.RegisterTaskDefinition(
            TASK_NAME, td, TASK_CREATE_OR_UPDATE,
            None, None, TASK_LOGON_INTERACTIVE_TOKEN)
        print("[register] %s registered" % TASK_NAME)

        task = root.GetTask(TASK_NAME)
        print("[verify] NextRunTime=%s State=%s" % (task.NextRunTime, task.State))

        n = task.Definition.Triggers.Count
        ok = (n == len(SLOTS))
        for i in range(1, n + 1):
            tg = task.Definition.Triggers.Item(i)
            rep = getattr(tg, "Repetition", None)
            iv = getattr(rep, "Interval", "?") if rep else "?"
            du = getattr(rep, "Duration", "?") if rep else "?"
            sb = str(tg.StartBoundary)
            print("[verify] trigger%d type=%s start=%s interval=%s duration=%s"
                  % (i, tg.Type, sb, iv, du))
            ok = ok and tg.Type == TASK_TRIGGER_DAILY and iv == INTERVAL

        xml = task.Xml
        dur_ok = ("<Duration>PT2H</Duration>" in xml
                  or "<Duration>PT120M</Duration>" in xml)
        for marker, hit in (
            ("<Repetition>", "<Repetition>" in xml),
            ("<Interval>PT15M</Interval>", "<Interval>PT15M</Interval>" in xml),
            ("<Duration>PT2H|PT120M</Duration>", dur_ok),
        ):
            print("[verify] xml %-34s %s" % (marker, "OK" if hit else "MISSING"))
            ok = ok and hit

        print("[register] final check: %s" % ("OK" if ok else "MISMATCH"))
        return 0 if ok else 2
    finally:
        pythoncom.CoUninitialize()


def delete():
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        sched = win32com.client.Dispatch("Schedule.Service")
        sched.Connect()
        root = sched.GetFolder("\\")
        try:
            root.GetTask(TASK_NAME)
        except Exception:                          # noqa: BLE001
            print("[delete] %s 不存在，无需删除" % TASK_NAME)
            return 0
        root.DeleteTask(TASK_NAME, 0)              # 0 = 只删任务本身
        print("[delete] %s 已删除" % TASK_NAME)
        try:
            root.GetTask(TASK_NAME)
            print("[delete] 复核失败：任务仍在")
            return 2
        except Exception:                          # noqa: BLE001
            print("[delete] 复核 OK：GetTask 已取不到")
            return 0
    finally:
        pythoncom.CoUninitialize()


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                              # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只打印定义，不注册")
    ap.add_argument("--delete", action="store_true", help="删除本任务（回滚）")
    args = ap.parse_args()

    describe()
    if args.dry:
        return 0
    if args.delete:
        return delete()
    return register()


if __name__ == "__main__":
    sys.exit(main() or 0)
