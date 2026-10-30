# -*- coding: utf-8 -*-
"""能力令牌 CLI（Phase A）：init / issue / verify。

用法：
  python tools/cap.py init
  python tools/cap.py issue --scope ide:write:WO-X --scope ide:read --task WO-X --sub agent --ttl 900
  python tools/cap.py verify <token> --scope ide:write:WO-X --task WO-X
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import secrets
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / "agent_platform" / "legion_ide" / "cap_token.py"


def _load():
    spec = importlib.util.spec_from_file_location("zb_cap_token", MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _init(args) -> int:
    p = Path(args.key_file or os.environ.get("LEGION_CAP_KEY_FILE")
             or _load().DEFAULT_KEY_FILE)
    if p.exists() and not args.force:
        print(json.dumps({"ok": False, "error": "key_exists", "path": str(p)}))
        return 1
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(secrets.token_bytes(32).hex().encode("ascii"))
    try:  # 仅当前用户可读（失败不阻断，路径在 _vault 下）
        subprocess.run(["icacls", str(p), "/inheritance:r", "/grant:r",
                        "%s:R" % os.environ.get("USERNAME", "")],
                       capture_output=True)
    except OSError:
        pass
    print(json.dumps({"ok": True, "path": str(p)}, ensure_ascii=False))
    return 0


def _issue(args) -> int:
    m = _load()
    scopes = []
    for s in args.scope or []:
        scopes.extend([x.strip() for x in str(s).split(",") if x.strip()])
    task = str(args.task or "")
    if task:
        scopes = [s if ":" in s and s.split(":")[-1] == task else s for s in scopes]
    token = m.issue(scopes, task=task, sub=args.sub, ttl=args.ttl)
    print(json.dumps({"ok": True, "token": token, "scopes": scopes,
                      "task": task, "ttl": args.ttl}, ensure_ascii=False))
    return 0


def _verify(args) -> int:
    m = _load()
    res = m.verify(args.token, required_scope=args.scope, task=args.task or "")
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0 if res.get("ok") else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="零界能力令牌")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--key-file", default="")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=_init)
    p = sub.add_parser("issue")
    p.add_argument("--scope", action="append", default=[])
    p.add_argument("--task", default="")
    p.add_argument("--sub", default="local")
    p.add_argument("--ttl", type=int, default=900)
    p.set_defaults(fn=_issue)
    p = sub.add_parser("verify")
    p.add_argument("token")
    p.add_argument("--scope", default="")
    p.add_argument("--task", default="")
    p.set_defaults(fn=_verify)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
