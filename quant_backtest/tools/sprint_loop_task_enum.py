# -*- coding: utf-8 -*-
"""Forensic dump of non-system scheduled tasks (pywin32 COM, read-only)."""
import sys
import datetime

import pythoncom
import win32com.client

OUT = sys.argv[1] if len(sys.argv) > 1 else "sprint_loop_task_enum_out.txt"

SYSTEM_FOLDERS = {"Microsoft"}
SKIP_NAME_PREFIX = tuple()

lines = []


def w(s):
    lines.append(s)


def fmt_time(t):
    # SYSTEMTIME tuple -> str
    try:
        if t is None:
            return "None"
        return "%04d-%02d-%02d %02d:%02d:%02d" % (
            t[0], t[1], t[2], t[3], t[4], t[5])
    except Exception:
        return str(t)


def fmt_trigger(tr):
    try:
        parts = ["Type=%d" % tr.Type]
        try:
            parts.append("StartBoundary=%s" % tr.StartBoundary)
        except Exception:
            pass
        try:
            parts.append("Enabled=%s" % tr.Enabled)
        except Exception:
            pass
        try:
            rep = tr.Repetition
            if rep is not None:
                interval = rep.Interval
                duration = rep.Duration
                stopat = rep.StopAtDurationEnd
                parts.append("Repetition[Interval=%s Duration=%s StopAtEnd=%s]"
                             % (interval, duration, stopat))
        except Exception:
            pass
        return " ".join(parts)
    except Exception as e:
        return "trigger-error: %s" % e


def main():
    pythoncom.CoInitialize()
    svc = win32com.client.Dispatch("Schedule.Service")
    svc.Connect()
    root = svc.GetFolder("\\")
    folders = [("", root)]
    seen_folders = set()
    while folders:
        path, folder = folders.pop(0)
        if path in seen_folders:
            continue
        seen_folders.add(path)
        try:
            subfs = folder.GetFolders(0)
        except Exception:
            subfs = []
        for i in range(1, subfs.Count + 1):
            sf = subfs.Item(i)
            fpath = (path + "\\" + sf.Name) if path else ("\\" + sf.Name)
            if sf.Name in SYSTEM_FOLDERS:
                continue
            folders.append((fpath, sf))
        try:
            tasks = folder.GetTasks(1)  # TASK_ENUM_HIDDEN = 1
        except Exception:
            tasks = []
        for i in range(1, tasks.Count + 1):
            t = tasks.Item(i)
            name = t.Name
            if name.lower().startswith(SKIP_NAME_PREFIX):
                pass
            full = (path + "\\" + name) if path else name
            try:
                state = t.State
            except Exception:
                state = "?"
            try:
                lrt = fmt_time(t.LastRunTime)
            except Exception:
                lrt = "?"
            try:
                ltr = t.LastTaskResult
            except Exception:
                ltr = "?"
            try:
                nrt = fmt_time(t.NextRunTime)
            except Exception:
                nrt = "?"
            w("=" * 70)
            w("TASK: %s" % full)
            w("  State=%s LastRunTime=%s LastResult=0x%08X NextRunTime=%s"
              % (state, lrt, ltr & 0xFFFFFFFF, nrt))
            try:
                act = t.Definition.Actions
                for j in range(1, act.Count + 1):
                    a = act.Item(j)
                    apath = getattr(a, "Path", None)
                    aargs = getattr(a, "Arguments", None)
                    w("  Action[%d]: Exec=%s Args=%s WorkDir=%s"
                      % (j, apath, aargs, getattr(a, "WorkingDirectory", "")))
            except Exception as e:
                w("  Action-error: %s" % e)
            try:
                trs = t.Definition.Triggers
                w("  Triggers count=%d" % trs.Count)
                for j in range(1, trs.Count + 1):
                    w("  Trigger[%d]: %s" % (j, fmt_trigger(trs.Item(j))))
            except Exception as e:
                w("  Trigger-error: %s" % e)
            try:
                w("  Principal: %s LogonType=%s RunLevel=%s"
                  % (t.Definition.Principal.UserId,
                     t.Definition.Principal.LogonType,
                     t.Definition.Principal.RunLevel))
            except Exception as e:
                w("  Principal-error: %s" % e)
            w("  XML:")
            try:
                for ln in t.Definition.XmlText.splitlines():
                    w("    " + ln)
            except Exception as e:
                w("    xml-error: %s" % e)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("generated %s\n" % datetime.datetime.now().isoformat())
        f.write("\n".join(lines))
    print("written:", OUT)


if __name__ == "__main__":
    main()
