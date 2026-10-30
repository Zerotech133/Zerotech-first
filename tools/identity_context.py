# -*- coding: utf-8 -*-
"""P3 IdentityContext CLI（只读）：列出/解析零界·智策·菜鸟 actor 映射。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PORTAL = REPO / "portal"
sys.path.insert(0, str(PORTAL))

import identity_registry as reg  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="IdentityContext registry（只读）")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--resolve", default="")
    ap.add_argument("--source", default="")
    ap.add_argument("--id", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.resolve:
        ctx = reg.resolve(args.resolve)
        if ctx is None:
            print(json.dumps({"ok": False, "reason": "identity_not_found", "sub": args.resolve}, ensure_ascii=False))
            return 1
        print(json.dumps({"ok": True, "identity": ctx}, ensure_ascii=False, indent=2))
        return 0
    if args.source and args.id:
        ctx = reg.resolve_source(args.source, args.id)
        if ctx is None:
            print(json.dumps({"ok": False, "reason": "identity_not_found",
                              "source": args.source, "id": args.id}, ensure_ascii=False))
            return 1
        print(json.dumps({"ok": True, "identity": ctx}, ensure_ascii=False, indent=2))
        return 0
    actors = reg.list_actors()
    if args.json:
        print(json.dumps({"ok": True, "actors": actors}, ensure_ascii=False, indent=2))
    else:
        for a in actors:
            links = ", ".join("%s:%s(%s)" % (l["system"], l["source_id"], l["status"]) for l in a["links"])
            print("%-24s %-8s %-10s %s  %s" % (
                a["sub"], a["actor_type"], a["source"], a.get("mapping_status", "?"), links))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
