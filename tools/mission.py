# -*- coding: utf-8 -*-
"""Mission MVP CLI：文档→plan；dry-run 派工；列表。

用法：
  python -X utf8 tools/mission.py plan --name "任务名" --doc doc.md [--no-llm] [--json]
  python -X utf8 tools/mission.py dry-run --plan work/missions/<id>/plan.json [--json]
  python -X utf8 tools/mission.py dry-run --mission <id> [--json]
  python -X utf8 tools/mission.py list [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agent_platform"))
from legion_command import mission_dispatch as D  # noqa: E402
from legion_command import mission_planner as P  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Mission MVP（阶段1）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("plan")
    a.add_argument("--name", required=True)
    a.add_argument("--doc", required=True)
    a.add_argument("--no-llm", action="store_true")
    a.add_argument("--json", action="store_true")
    d = sub.add_parser("dry-run")
    d.add_argument("--plan", default="")
    d.add_argument("--mission", default="")
    d.add_argument("--parallel", type=int, default=3)
    d.add_argument("--json", action="store_true")
    l = sub.add_parser("list")
    l.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "plan":
        doc = Path(args.doc).read_text(encoding="utf-8", errors="replace")
        res = P.plan(args.name, doc, use_llm=not args.no_llm)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=1))
        else:
            print("mission", res["mission_id"], "source", res["source"],
                  "valid", res["valid"], "tasks", len(res["tasks"]))
            print("plan", Path("work/missions") / res["mission_id"] / "plan.json")
            for t in res["tasks"]:
                print(" %-4s %-6s %-34s deps=%s" % (t["id"], t["owner"], t["title"], t["deps"]))
        return 0

    if args.cmd == "dry-run":
        if args.plan:
            plan = json.loads(Path(args.plan).read_text(encoding="utf-8-sig"))
        elif args.mission:
            plan = P.load_plan(args.mission)
        else:
            print("需要 --plan 或 --mission")
            return 2
        res = D.dry_run(plan, args.parallel)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=1))
        else:
            print("mission", res["mission_id"], "mode", res["mode"],
                  "ok", res["ok"], "conflicts", len(res["conflicts"]))
            for r in res["rows"]:
                print(" %s. %-4s %-6s %-32s deps=%s" % (r["step"], r["task_id"], r["owner"],
                                                         r["title"], r["deps"]))
                print("     ticket:", r["ticket_id"])
            if res["conflicts"]:
                print("scope conflicts:", json.dumps(res["conflicts"], ensure_ascii=False))
        return 0 if res["ok"] else 1

    rows = P.list_missions()
    print(json.dumps(rows, ensure_ascii=False, indent=1) if args.json else
          "\n".join("%-28s %-6s tasks=%-2d %s %s" % (r["mission_id"], r["source"],
                                                      r["tasks"], r["created"], r["name"])
                    for r in rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
