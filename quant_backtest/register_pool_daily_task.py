# -*- coding: utf-8 -*-
"""注册两个每日任务（pywin32 COM，2026-11-09，老田授权）。

为什么不用 schtasks / Register-ScheduledTask：宿主拦截这两个入口
（同 register_timeline_watch.py、register_pool_judge_task.py 判例），
TaskScheduler COM 是唯一能走通的路。

两个任务各管一段，时间上不重叠：

  PoolCloseReview    15:02  收盘复盘：用收盘价重算 judge、串一天十几份快照、
                            推一张「今天说了什么、后来怎么样」到飞书。
                            15:02 跑是因为此刻没有日线（要等 16:47），
                            用的是收盘定格价，不需要新结构。
  PoolDailyPipeline  18:30  日线到齐后重算结构、板块层、成分快照、两个页面。
                            这个时刻页面才变成「当天的新结构」。

为什么不合成一个：15:02 时 data/ashare 还没有今天的日线，硬跑结构重算只会
用昨天的数据算今天的结构；而 18:30 再推一张卡，会跟 15:02 那张内容重复。
两件事的自变量不同，不该挤在一个触发点上。

两个任务都设 StartWhenAvailable=True：错过（休眠/关机）就补跑，因为下游
各自都有门控兜底——pool_daily 检查日线是不是今天的，pool_close_review
检查是否在 15:00~17:00 且行情时点是今天。补跑跑到不该跑的时段会自己退出，
不会推过期的东西。这和盘中任务（PoolJudgeIntraday，StartWhenAvailable=False）
正好相反：盘中价过期就是错价，补跑毫无意义。

身份 InteractiveToken（不存密码）：机器没登录时不会跑。这是当前账户策略下
唯一能用的方式；改 S4U 要动凭据策略，没做。

用法:
  python register_pool_daily_task.py --dry      # 只打印将要注册的定义
  python register_pool_daily_task.py            # 注册/覆盖（只碰这两个任务）
  python register_pool_daily_task.py --delete   # 删除（回滚）
"""

import argparse
import sys
from datetime import datetime

REPO = r"C:\项目\老田的AI助手\quant_backtest"
PYTHON = r"C:\Program Files\Python311\python.exe"

# (任务名, 脚本, 时刻, 说明, 单轮上限)
TASKS = [
    ("PoolCloseReview", "pool_close_review.py", "15:02",
     "Chan pool close review: recompute judge at close, trace the day's "
     "snapshots, push one Feishu card. Owned by pool_close_review.",
     "PT20M"),
    ("PoolDailyPipeline", "pool_daily.py", "18:30",
     "Chan pool daily pipeline after EOD bars: rescan structure, sector "
     "layer, member snapshot, regenerate both H5 pages. Owned by pool_daily.",
     "PT30M"),
]

TASK_ACTION_EXEC = 0
TASK_TRIGGER_DAILY = 2
TASK_CREATE_OR_UPDATE = 6
TASK_LOGON_INTERACTIVE_TOKEN = 3
TASK_RUNLEVEL_LUA = 0


def describe():
    for name, script, at, desc, limit in TASKS:
        print("[plan] task   %s" % name)
        print("[plan]   action  %s \"%s\\%s\"" % (PYTHON, REPO, script))
        print("[plan]   trigger daily %s · 补跑开 · IgnoreNew · 上限 %s"
              % (at, limit))


def _make(sched, name, script, at, desc, limit):
    root = sched.GetFolder("\\")
    td = sched.NewTask(0)
    td.RegistrationInfo.Description = desc
    td.RegistrationInfo.Author = "pool-daily"

    trig = td.Triggers.Create(TASK_TRIGGER_DAILY)
    trig.Id = "DailyTrigger"
    trig.StartBoundary = "%sT%s:00" % (datetime.now().strftime("%Y-%m-%d"), at)
    trig.DaysInterval = 1
    trig.Enabled = True

    act = td.Actions.Create(TASK_ACTION_EXEC)
    act.Id = "DailyAction"
    act.Path = PYTHON
    act.Arguments = '"%s\\%s"' % (REPO, script)
    act.WorkingDirectory = REPO

    td.Principal.LogonType = TASK_LOGON_INTERACTIVE_TOKEN
    td.Principal.RunLevel = TASK_RUNLEVEL_LUA
    td.Settings.Enabled = True
    td.Settings.StartWhenAvailable = True     # 下游有门控，补跑安全
    td.Settings.MultipleInstances = 2         # IgnoreNew
    td.Settings.ExecutionTimeLimit = limit
    td.Settings.DisallowStartIfOnBatteries = False
    td.Settings.StopIfGoingOnBatteries = False

    root.RegisterTaskDefinition(name, td, TASK_CREATE_OR_UPDATE,
                                None, None, TASK_LOGON_INTERACTIVE_TOKEN)
    t = root.GetTask(name)
    tg = t.Definition.Triggers.Item(1)
    xml = t.Xml
    ok = (str(tg.StartBoundary).endswith(at + ":00")
          and "<StartWhenAvailable>true</StartWhenAvailable>" in xml)
    print("[register] %-20s start=%s next=%s state=%s -> %s"
          % (name, str(tg.StartBoundary)[11:16], t.NextRunTime, t.State,
             "OK" if ok else "MISMATCH"))
    return ok


def register():
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        sched = win32com.client.Dispatch("Schedule.Service")
        sched.Connect()
        allok = True
        for name, script, at, desc, limit in TASKS:
            allok = _make(sched, name, script, at, desc, limit) and allok
        print("[register] final check: %s" % ("OK" if allok else "MISMATCH"))
        return 0 if allok else 2
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
        rc = 0
        for name, _, _, _, _ in TASKS:
            try:
                root.GetTask(name)
            except Exception:                      # noqa: BLE001
                print("[delete] %s 不存在，跳过" % name)
                continue
            root.DeleteTask(name, 0)
            try:
                root.GetTask(name)
                print("[delete] %s 复核失败：任务仍在" % name)
                rc = 2
            except Exception:                      # noqa: BLE001
                print("[delete] %s 已删除（复核 OK）" % name)
        return rc
    finally:
        pythoncom.CoUninitialize()


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                              # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只打印定义，不注册")
    ap.add_argument("--delete", action="store_true", help="删除这两个任务（回滚）")
    args = ap.parse_args()

    describe()
    if args.dry:
        return 0
    if args.delete:
        return delete()
    return register()


if __name__ == "__main__":
    sys.exit(main() or 0)
