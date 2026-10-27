# -*- coding: utf-8 -*-
"""CLI：席位路由/虚拟 Key 管理。

用法：
  python -X utf8 tools/seat_router.py aliases [--json]
  python -X utf8 tools/seat_router.py resolve --model auto [--token vk-...] [--json]
  python -X utf8 tools/seat_router.py vk create --owner tianfeng [--alias strong] [--allow auto strong]
  python -X utf8 tools/seat_router.py vk list [--json]
  python -X utf8 tools/seat_router.py vk disable <id>
虚拟 Key 明文只在 create 时返回一次；之后只显示掩码。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agent_platform"))
from legion_command import seat_router as R  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="席位路由/虚拟 Key")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("aliases")
    a.add_argument("--json", action="store_true")
    r = sub.add_parser("resolve")
    r.add_argument("--model", default="auto")
    r.add_argument("--token", default="")
    r.add_argument("--json", action="store_true")
    v = sub.add_parser("vk")
    vs = v.add_subparsers(dest="vkcmd", required=True)
    c = vs.add_parser("create")
    c.add_argument("--owner", required=True)
    c.add_argument("--alias", default="auto")
    c.add_argument("--allow", nargs="*", default=None)
    l = vs.add_parser("list")
    l.add_argument("--json", action="store_true")
    d = vs.add_parser("disable")
    d.add_argument("id")
    args = ap.parse_args(argv)
    if args.cmd == "aliases":
        cfg = R.load_config()
        print(json.dumps(cfg, ensure_ascii=False, indent=1) if args.json else
              json.dumps(cfg, ensure_ascii=False))
        return 0
    if args.cmd == "resolve":
        res = R.resolve(args.model, args.token)
        print(json.dumps(res, ensure_ascii=False, indent=1) if args.json else
              json.dumps(res, ensure_ascii=False))
        return 0 if res.get("ok") else 2
    if args.vkcmd == "create":
        try:
            item = R.create_vk(args.owner, args.alias, args.allow)
        except ValueError as e:
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
            return 2
        print(json.dumps(item, ensure_ascii=False, indent=1))
        return 0
    if args.vkcmd == "list":
        rows = R.list_vk()
        print(json.dumps(rows, ensure_ascii=False, indent=1) if args.json else
              "\n".join("%s owner=%s alias=%s enabled=%s masked=%s"
                        % (x["id"], x["owner"], x["alias"], x["enabled"], x.get("masked"))
                        for x in rows))
        return 0
    try:
        item = R.disable_vk(args.id)
    except ValueError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 2
    print(json.dumps(item, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
