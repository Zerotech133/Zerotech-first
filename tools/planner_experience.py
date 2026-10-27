# -*- coding: utf-8 -*-
"""Planner 经验规则 CLI：status / build-rules / ab。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agent_platform"))
from legion_command import planner_rules as P  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Planner 签字经验规则 / A-B")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("build-rules")
    b = sub.add_parser("ab")
    b.add_argument("--doc", required=True)
    b.add_argument("--name", default="A/B 对比")
    args = ap.parse_args(argv)
    if args.cmd == "status":
        print(json.dumps({"signed": P.signed_rules(),
                          "candidate": P.candidate_rules(),
                          "active": P.active_rules()}, ensure_ascii=False, indent=1))
    elif args.cmd == "build-rules":
        print(json.dumps(P.build_rules(), ensure_ascii=False, indent=1))
    else:
        doc = Path(args.doc).read_text(encoding="utf-8", errors="replace")
        print(json.dumps(P.ab_compare(doc, args.name), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
