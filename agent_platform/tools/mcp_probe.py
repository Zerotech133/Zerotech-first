# -*- coding: utf-8 -*-
"""MCP 探测/调用 CLI：python agent_platform/tools/mcp_probe.py --json|--selftest|--call ..."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PLATFORM = Path(__file__).resolve().parents[2] / "agent_platform"
sys.path.insert(0, str(PLATFORM))

from legion_mcp import registry  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="赵云 MCP 工具探测")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--call", nargs=2, metavar=("SERVER", "TOOL"))
    ap.add_argument("--args", default="{}", help="JSON arguments for --call")
    ap.add_argument("--timeout", type=float, default=30.0)
    args = ap.parse_args(argv)
    if args.call:
        try:
            payload = json.loads(args.args)
        except ValueError:
            print(
                json.dumps({"ok": False, "error": "bad_args_json"}, ensure_ascii=False)
            )
            return 2
        rep = registry.call(args.call[0], args.call[1], payload, timeout=args.timeout)
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return 0 if rep.get("ok") else 1
    if args.selftest:
        rep = registry.selftest()
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return 0 if rep.get("ok") else 1
    rep = registry.summary(force=args.force)
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
