# -*- coding: utf-8 -*-
"""Register XueqiuBackfillSprint as a daily 13:30 recurring task (pywin32 COM).

2026-10-18 sprint-loop 工位：把一次性 TimeTrigger 改为每日 13:30 日历触发器
（无 EndBoundary，无限期；无 Repetition/Duration——避免 Repetition 带
Duration 静默停摆的坑，窗口约束由 runner 自身 13:30-17:30 让路语义承担）。

红线：绝不 Start 任务。验证 = 注册后读 NextRunTime 属性 + 直读任务 XML
断言每日触发器锚点。StartBoundary 显式取明天（2026-10-19）13:30，避免
StartWhenAvailable=true 把「今天已错过的当日槽位」立即补跑。

附带一处设置调整：ExecutionTimeLimit PT4H→PT5H。窗口 13:30-17:30 恰好
4h，旧值会在 17:30 整点硬杀仍在收尾分片的进程（留陈锁）；放宽 1h 让
runner 自己的窗口检查（17:30 后不再开新片）优雅退出。
"""

import sys

import pythoncom
import win32com.client

TASK_NAME = "XueqiuBackfillSprint"
START_BOUNDARY = "2026-10-19T13:30:00"  # 明日起每日 13:30（本地时间）
TASK_CREATE_OR_UPDATE = 6
TASK_LOGON_INTERACTIVE_TOKEN = 3
TASK_TRIGGER_DAILY = 2

OUT = sys.argv[1] if len(sys.argv) > 1 else "sprint_loop_register_out.txt"
lines = []


def w(s):
    lines.append(str(s))
    print(s)


def main():
    pythoncom.CoInitialize()
    svc = win32com.client.Dispatch("Schedule.Service")
    svc.Connect()
    root = svc.GetFolder("\\")

    task = root.GetTask(TASK_NAME)
    defn = task.Definition

    w("before: triggers=%d" % defn.Triggers.Count)
    for i in range(1, defn.Triggers.Count + 1):
        tr = defn.Triggers.Item(i)
        w("  old trigger[%d]: type=%d start=%s" % (i, tr.Type, tr.StartBoundary))
    for i in range(defn.Triggers.Count, 0, -1):
        defn.Triggers.Remove(i)

    trig = defn.Triggers.Create(TASK_TRIGGER_DAILY)
    trig.Id = "sprint_daily_1330"
    trig.StartBoundary = START_BOUNDARY
    trig.DaysInterval = 1
    trig.Enabled = True
    # 不设 Repetition / EndBoundary：每日触发一次，无限期

    defn.RegistrationInfo.Description = (
        "Xueqiu classify backfill sprint, daily 13:30 (runner self-limits "
        "window 13:30-17:30, lock-protected, yields to automation; "
        "re-registered 2026-10-18 from one-shot)"
    )
    defn.Settings.ExecutionTimeLimit = "PT5H"

    root.RegisterTaskDefinition(
        TASK_NAME, defn, TASK_CREATE_OR_UPDATE, None, None, TASK_LOGON_INTERACTIVE_TOKEN
    )
    w("registered.")

    # ---- verify ----
    task2 = root.GetTask(TASK_NAME)
    d2 = task2.Definition
    nrt = task2.NextRunTime
    w("NextRunTime: %r" % (nrt,))
    ok_nrt = (
        nrt is not None
        and nrt.year == 2026
        and nrt.month == 10
        and nrt.day == 19
        and nrt.hour == 13
        and nrt.minute == 30
    )
    w("NextRunTime==2026-10-19 13:30: %s" % ok_nrt)

    trs = d2.Triggers
    w("triggers count=%d (anchor==1: %s)" % (trs.Count, trs.Count == 1))
    for i in range(1, trs.Count + 1):
        tr = trs.Item(i)
        w(
            "  trigger[%d]: type=%d start=%s enabled=%s"
            % (i, tr.Type, tr.StartBoundary, tr.Enabled)
        )
        try:
            rep = tr.Repetition
            w("  repetition: interval=%r duration=%r" % (rep.Interval, rep.Duration))
        except Exception as e:
            w("  repetition: n/a (%s)" % e)
    ok_trig = trs.Count == 1 and trs.Item(1).Type == TASK_TRIGGER_DAILY

    xml = d2.XmlText
    has_daily = "<ScheduleByDay>" in xml and "<DaysInterval>1</DaysInterval>" in xml
    has_boundary = "2026-10-19T13:30:00" in xml
    n_cal = xml.count("<CalendarTrigger")
    one_calendar = n_cal == 1
    w(
        "XML anchors: ScheduleByDay+DaysInterval1=%s boundary=%s "
        "calendar_trigger_count=%d" % (has_daily, has_boundary, n_cal)
    )
    w("ExecutionTimeLimit in XML: %s" % ("PT5H" in xml))
    ok_xml = has_daily and has_boundary and one_calendar

    w(
        "Principal: %s logon=%s runlevel=%s"
        % (d2.Principal.UserId, d2.Principal.LogonType, d2.Principal.RunLevel)
    )
    w("Action: %s %s" % (d2.Actions.Item(1).Path, d2.Actions.Item(1).Arguments))

    all_ok = ok_nrt and ok_trig and ok_xml
    w("RESULT: %s" % ("PASS" if all_ok else "FAIL"))
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
