# -*- coding: utf-8 -*-
"""任务单 CLI（K-ORG-011）：open / ls / move

用法：
    python task.py open --to dev --title "..." --body "..." [--sev P1] [--by A0] [--due 2026-10-16] [--p0]
    python task.py ls [--dept dev] [--status open]
    python task.py move <task_id> <new_status> --by dept/dev/DEV1 [--note "..."]
"""
import argparse
import sys
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE / "engine"))
from tasks import TaskError, TaskStore, PermissionDenied  # noqa: E402

STORE = TaskStore(BASE / "data" / "org" / "tasks")


def main():
    ap = argparse.ArgumentParser(description="K-ORG 任务单")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_open = sub.add_parser("open")
    p_open.add_argument("--to", required=True, dest="to_dept")
    p_open.add_argument("--title", required=True)
    p_open.add_argument("--body", required=True)
    p_open.add_argument("--sev", default="P2", dest="severity")
    p_open.add_argument("--by", default="A0")
    p_open.add_argument("--due", default=None)
    p_open.add_argument("--p0", action="store_true")

    p_ls = sub.add_parser("ls")
    p_ls.add_argument("--dept", default=None)
    p_ls.add_argument("--status", default=None)

    p_mv = sub.add_parser("move")
    p_mv.add_argument("task_id")
    p_mv.add_argument("new_status")
    p_mv.add_argument("--by", required=True)
    p_mv.add_argument("--note", default=None)

    args = ap.parse_args()
    try:
        if args.cmd == "open":
            tid = STORE.open(args.to_dept, args.title, args.body, args.severity,
                             opened_by=args.by, due=args.due, p0=args.p0)
            print("[task] 已开单 %s → %s（%s）" % (tid, args.to_dept, args.severity))
        elif args.cmd == "ls":
            rows = STORE.ls(dept=args.dept, status=args.status)
            print("%-16s %-4s %-8s %-8s %s" % ("task_id", "sev", "dept", "status", "title"))
            for t in rows:
                print("%-16s %-4s %-8s %-8s %s" % (
                    t["task_id"], t["severity"], t["assigned_dept"], t["status"], t["title"][:36]))
            print("共 %d 单" % len(rows))
        elif args.cmd == "move":
            t = STORE.move(args.task_id, args.new_status, by=args.by, note=args.note)
            print("[task] %s → %s" % (t["task_id"], t["status"]))
    except (TaskError, PermissionDenied) as e:
        print("[task] 拒绝：%s" % e)
        sys.exit(2)


if __name__ == "__main__":
    main()
