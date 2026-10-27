# -*- coding: utf-8 -*-
"""实现/验收分离审计（只读）：dev 单是否有独立验收人与验收命令。

用法：
  python -X utf8 tools/dev_acceptance_audit.py
  python -X utf8 tools/dev_acceptance_audit.py --json
  python -X utf8 tools/dev_acceptance_audit.py --strict   # 有缺口 rc=1（暂不接合并门）
规则见 docs/standards/2026-10-27_实现与验收分离_v1.md。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TASKS = os.path.join(REPO, ".worktrees", "tasks.json")
ACCEPT_OWNER_RE = re.compile(r"验收人\s*[=:：]\s*([A-Za-z0-9_·\-]+)")
ACTIVE = ("doing", "review")


def parse_acceptance_owner(desc):
    m = ACCEPT_OWNER_RE.search(str(desc or ""))
    return m.group(1) if m else None


def is_dev_ticket(tid, t):
    if str(t.get("dept") or "") == "dev":
        return True
    if str(tid).startswith("WO-DEV-"):
        return True
    return "--dept dev" in str(t.get("desc") or "")


def evaluate(tasks):
    rows = []
    for tid, t in (tasks or {}).items():
        if not isinstance(t, dict) or not is_dev_ticket(tid, t):
            continue
        status = str(t.get("status") or "")
        desc = str(t.get("desc") or "")
        acc_cmd = str(t.get("acceptance_cmd") or "").strip()
        owner = str(t.get("owner") or "").strip()
        acc_owner = parse_acceptance_owner(desc)
        missing = []
        if not acc_cmd:
            missing.append("acceptance_cmd")
        if not acc_owner:
            missing.append("验收人")
        elif owner and acc_owner.lower() == owner.lower():
            missing.append("验收人=实现者")
        rows.append({
            "id": tid,
            "status": status,
            "owner": owner,
            "acceptance_owner": acc_owner,
            "has_acceptance_cmd": bool(acc_cmd),
            "issues": missing,
            "ok": not missing,
            "active": status in ACTIVE,
        })
    total = len(rows)
    active = [r for r in rows if r["active"]]
    return {
        "total_dev": total,
        "active_dev": len(active),
        "ok": sum(1 for r in rows if r["ok"]),
        "missing_acceptance_cmd": sum(1 for r in rows if not r["has_acceptance_cmd"]),
        "missing_acceptance_owner": sum(1 for r in rows if not r["acceptance_owner"]),
        "acceptance_owner_equals_implementer": sum(
            1 for r in rows if "验收人=实现者" in r["issues"]),
        "active_issues": sum(1 for r in active if not r["ok"]),
        "rows": rows,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="实现/验收分离审计（只读）")
    ap.add_argument("--tasks", default=DEFAULT_TASKS)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="有缺口 rc=1（当前不接合并门）")
    ap.add_argument("--active-only", action="store_true", help="只看 doing/review")
    args = ap.parse_args(argv)
    if not os.path.exists(args.tasks):
        print("tasks.json 不存在：%s" % args.tasks, file=sys.stderr)
        return 2
    try:
        with io.open(args.tasks, "r", encoding="utf-8") as f:
            tasks = json.load(f)
    except (OSError, ValueError) as e:
        print("tasks.json 解析失败：%s" % e, file=sys.stderr)
        return 2
    res = evaluate(tasks)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print("dev acceptance audit: dev=%d active=%d ok=%d" % (
            res["total_dev"], res["active_dev"], res["ok"]))
        print("  missing_acceptance_cmd=%d missing_acceptance_owner=%d acceptance_owner_equals_implementer=%d" % (
            res["missing_acceptance_cmd"], res["missing_acceptance_owner"],
            res["acceptance_owner_equals_implementer"]))
        for r in res["rows"]:
            if args.active_only and not r["active"]:
                continue
            mark = "OK " if r["ok"] else "WARN"
            print("  [%s] %-34s owner=%-10s acc_owner=%-8s issues=%s" % (
                mark, r["id"], r["owner"] or "-", r["acceptance_owner"] or "-",
                ",".join(r["issues"]) or "-"))
    if args.strict and (res["missing_acceptance_cmd"] or res["missing_acceptance_owner"]
                        or res["acceptance_owner_equals_implementer"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
