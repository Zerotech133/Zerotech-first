# -*- coding: utf-8 -*-
"""任务板 stale 清理（默认 dry-run；--apply 需 --yes 且仅限安全类）。

安全类定义：branch_missing（分支不存在、worktree 不存在）且 owner 在白名单。
其余（ahead_unmerged/merged_but_open/doing/review）只报告，不自动改。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

REPO = _HERE.parent
TASKS = REPO / ".worktrees" / "tasks.json"
DEFAULT_OWNERS = {"ZHAOYUN", "DSH-Lead"}
STALE_DAYS = 7


def _age_days(created: str, now: dt.datetime) -> int | None:
    try:
        c = dt.datetime.strptime(str(created), "%Y-%m-%d %H:%M:%S")
        return max(0, (now - c).days)
    except (TypeError, ValueError):
        return None


def classify(tasks: dict, board: dict, now: dt.datetime | None = None,
             owners: set[str] | None = None) -> list[dict]:
    now = now or dt.datetime.now()
    owners = owners or DEFAULT_OWNERS
    rows = []

    for tid in (board.get("branch_missing") or []):
        v = tasks.get(tid) or {}
        rows.append({
            "task": tid, "owner": v.get("owner"), "status": v.get("status"),
            "age_days": _age_days(v.get("created"), now),
            "kind": "branch_missing",
            "action": "abort" if str(v.get("status")) in ("doing", "review") else "close",
            "safe": v.get("owner") in owners,
        })
    for tid in (board.get("merged_but_open") or []):
        v = tasks.get(tid) or {}
        rows.append({
            "task": tid, "owner": v.get("owner"), "status": v.get("status"),
            "age_days": _age_days(v.get("created"), now),
            "kind": "merged_but_open", "action": "close", "safe": False,
        })
    for tid in (board.get("ahead_unmerged") or []):
        v = tasks.get(tid) or {}
        rows.append({
            "task": tid, "owner": v.get("owner"), "status": v.get("status"),
            "age_days": _age_days(v.get("created"), now),
            "kind": "ahead_unmerged", "action": "review", "safe": False,
        })
    seen = {r["task"] for r in rows}
    for tid, v in tasks.items():
        if tid in seen:
            continue
        if str(v.get("status")) not in ("doing", "review"):
            continue
        age = _age_days(v.get("created"), now)
        if age is not None and age >= STALE_DAYS:
            rows.append({"task": tid, "owner": v.get("owner"), "status": v.get("status"),
                         "age_days": age, "kind": "stale_in_progress",
                         "action": "check", "safe": False})
    return rows


def apply_safe(rows: list[dict], tasks_path: Path, now: dt.datetime | None = None) -> list[str]:
    now = now or dt.datetime.now()
    data = json.loads(tasks_path.read_text(encoding="utf-8-sig"))
    changed = []
    for r in rows:
        if r["kind"] != "branch_missing" or not r["safe"]:
            continue
        v = data.get(r["task"])
        if not v:
            continue
        v["status"] = "aborted"
        v["note"] = (v.get("note") or "") + " | %s stale清理: 分支/worktree缺失且无进展" % now.strftime("%Y-%m-%d")
        changed.append(r["task"])
    if changed:
        tasks_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return changed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="任务板 stale 清理（默认 dry-run）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--tasks", default=str(TASKS))
    args = ap.parse_args(argv)
    tasks = json.loads(Path(args.tasks).read_text(encoding="utf-8-sig"))
    import task_board_audit as tba
    board = tba.audit()
    rows = classify(tasks, board)
    if args.apply:
        if not args.yes:
            print("--apply 需同时 --yes", file=sys.stderr)
            return 2
        changed = apply_safe(rows, Path(args.tasks))
        print("applied:", json.dumps(changed, ensure_ascii=False))
    if args.json:
        print(json.dumps({"ok": True, "items": rows,
                          "summary": {"total": len(rows),
                                      "safe_abort": sum(1 for r in rows if r["safe"] and r["kind"] == "branch_missing"),
                                      "stale_in_progress": sum(1 for r in rows if r["kind"] == "stale_in_progress")}},
                         ensure_ascii=False, indent=1))
    else:
        print("stale 报告：共 %d 项；可安全 abort %d 项" % (
            len(rows), sum(1 for r in rows if r["safe"] and r["kind"] == "branch_missing")))
        for r in rows[:30]:
            print(" - [%s] %s owner=%s status=%s age=%s safe=%s action=%s" % (
                r["kind"], r["task"], r["owner"], r["status"], r["age_days"], r["safe"], r["action"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
