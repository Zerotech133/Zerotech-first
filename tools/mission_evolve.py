# -*- coding: utf-8 -*-
"""Mission 阶段3 CLI：采集/汇总/进化建议/候选/签字入库。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agent_platform"))
from legion_command import mission_evolver as E  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Mission 阶段3：结果→经验→策略建议")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("collect")
    sub.add_parser("summary")
    sub.add_parser("evolve")
    sub.add_parser("candidates")
    p = sub.add_parser("promote")
    p.add_argument("--candidate", required=True)
    p.add_argument("--signed-by", required=True)
    p.add_argument("--note", default="")
    args = ap.parse_args(argv)
    if args.cmd == "collect":
        print(json.dumps(E.collect(), ensure_ascii=False, indent=1))
    elif args.cmd == "summary":
        print(json.dumps(E.summarize(E.collect()), ensure_ascii=False, indent=1))
    elif args.cmd == "evolve":
        d = E.evolve()
        print(json.dumps({"candidate_id": d["candidate_id"], "metrics": d["metrics"],
                          "suggestions": d["suggestions"]}, ensure_ascii=False, indent=1))
    elif args.cmd == "candidates":
        print(json.dumps(E.list_candidates(), ensure_ascii=False, indent=1))
    else:
        print(json.dumps(E.promote(args.candidate, args.signed_by, args.note),
                         ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
