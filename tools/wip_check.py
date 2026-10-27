# -*- coding: utf-8 -*-
"""交付型席位 WIP 检查（只读 tasks.json + org.json）。

规则（§6.2）：交付型席位 doing + review <= 3；超过告警。
用法：
  python -X utf8 tools/wip_check.py
  python -X utf8 tools/wip_check.py --strict   # 超限 rc=1
  python -X utf8 tools/wip_check.py --json
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TASKS = os.path.join(REPO, ".worktrees", "tasks.json")
DEFAULT_ORG = os.path.join(REPO, "agent_platform", "registry", "org.json")
LIMIT = 3
# 交付型部门（name 或 dept 短名均可匹配）
DELIVERY_DEPTS = ("研发部", "质量工程部", "产品管理部", "回测开发部", "数据治理部",
                  "下版本设计推理部", "dev", "quality", "product",
                  "proj_backtest", "proj_data", "proj_next")


def load_seats(org_path):
    org = json.load(io.open(org_path, encoding="utf-8"))
    seats = set()
    dept_of = {}
    for d in org.get("departments") or []:
        name = str(d.get("name") or d.get("dept") or "")
        if not any(k in name for k in DELIVERY_DEPTS) and str(d.get("dept")) not in DELIVERY_DEPTS:
            continue
        for a in d.get("agents") or []:
            seats.add(a)
            dept_of[a] = name
    return seats, dept_of


def analyze(tasks, seats, limit=LIMIT):
    rows = []
    for tid, t in (tasks or {}).items():
        owner = str(t.get("owner") or "")
        if owner not in seats:
            continue
        st = str(t.get("status") or "")
        if st not in ("doing", "review"):
            continue
        rows.append({"task": tid, "owner": owner, "status": st,
                     "desc": str(t.get("desc") or "")[:80]})
    by_owner = {}
    for r in rows:
        by_owner.setdefault(r["owner"], []).append(r)
    over = [{"owner": o, "wip": len(v), "tasks": [x["task"] for x in v]}
            for o, v in by_owner.items() if len(v) > limit]
    over.sort(key=lambda x: -x["wip"])
    return {"limit": limit, "owners": {o: len(v) for o, v in by_owner.items()},
            "over": over, "ok": not over, "total_wip": len(rows)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="交付型席位 WIP 检查（只读）")
    ap.add_argument("--tasks", default=DEFAULT_TASKS)
    ap.add_argument("--org", default=DEFAULT_ORG)
    ap.add_argument("--limit", type=int, default=LIMIT)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args(argv)
    if not os.path.exists(args.tasks) or not os.path.exists(args.org):
        print("tasks.json 或 org.json 不存在", file=sys.stderr)
        return 2
    tasks = json.load(io.open(args.tasks, encoding="utf-8"))
    seats, _ = load_seats(args.org)
    res = analyze(tasks, seats, args.limit)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print("wip check: limit=%d seats_with_wip=%d total_wip=%d over=%d" % (
            res["limit"], len(res["owners"]), res["total_wip"], len(res["over"])))
        for o in res["over"]:
            print("  OVER %-10s wip=%d tasks=%s" % (o["owner"], o["wip"], ",".join(o["tasks"])))
    if args.strict and not res["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
