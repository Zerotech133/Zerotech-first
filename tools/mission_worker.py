# -*- coding: utf-8 -*-
"""Mission Worker CLI：自动接单低风险任务 → 在 worktree 写文件 → 语法检查 → commit。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agent_platform"))
from legion_command import mission_worker as W  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Mission Worker（低风险自动写码）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--mission", required=True)
    r.add_argument("--task", required=True)
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--json", action="store_true")
    p = sub.add_parser("poll")
    p.add_argument("--mission", required=True)
    p.add_argument("--limit", type=int, default=1)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "run":
        res = W.run_task(args.mission, args.task, args.dry_run)
    else:
        res = W.poll(args.mission, args.limit, args.dry_run)
    print(json.dumps(res, ensure_ascii=False, indent=1) if args.json else
          ("ok" if res.get("ok") else "FAILED: " + str(res.get("error"))))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
