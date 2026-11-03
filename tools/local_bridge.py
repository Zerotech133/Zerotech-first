#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地桥（stdio JSON-RPC v1）：桌面 Agent/本地进程调用本地库工具。

- 不新开端口、不暴露网络；
- 每次一行 JSON 请求，返回一行 JSON；
- 只允许 _local_libraries/<user>/ 固定根；不接受任意 path；
- cloud_brief 必须显式 authorize=true，且只返回 summary/evidence（无原文/路径/root）。

请求：
  {"id":"1","action":"users"}
  {"id":"2","action":"brief","user":"tf"}
  {"id":"3","action":"cloud_brief","user":"tf","authorize":true}
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))
import local_library as ll  # noqa: E402

BASE = Path(os.environ.get("ZB_LOCAL_LIBRARIES", r"C:\项目\_local_libraries"))
SCHEMA = "hq/local_bridge/v1"
USER_RE = re.compile(r"^[A-Za-z0-9._@-]{1,80}$")


def _user_dir(user: str) -> Path:
    u = str(user or "").strip()
    if not u or u in (".", "..") or not USER_RE.fullmatch(u):
        raise ValueError("bad_user")
    p = (BASE / u).resolve()
    try:
        p.relative_to(BASE.resolve())
    except ValueError as e:
        raise ValueError("user_outside") from e
    if not p.is_dir():
        raise ValueError("user_not_found")
    return p


def handle(req: dict, base: Path | None = None) -> dict:
    global BASE
    if base is not None:
        BASE = Path(base)
    rid = str((req or {}).get("id") or "")
    try:
        action = str((req or {}).get("action") or "")
        if action == "users":
            users = []
            if BASE.is_dir():
                for p in sorted(BASE.iterdir()):
                    if not p.is_dir() or p.name.startswith("_"):
                        continue
                    mf = ll.load_manifest(p)
                    if mf:
                        users.append({"user_id": mf.get("user_id") or p.name,
                                      "display_name": mf.get("display_name") or "",
                                      "sources": mf.get("sources") or []})
            return {"id": rid, "ok": True, "schema": SCHEMA, "users": users}
        user = str((req or {}).get("user") or "")
        p = _user_dir(user)
        if action == "brief":
            return {"id": rid, "ok": True, "schema": SCHEMA, "brief": ll.build_brief(user, p)}
        if action == "cloud_brief":
            if (req or {}).get("authorize") is not True:
                return {"id": rid, "ok": False, "error": "authorize_required"}
            b = ll.build_brief(user, p)
            cp = ll.cloud_payload(b)
            cp["privacy"]["confirmed"] = True
            return {"id": rid, "ok": True, "schema": SCHEMA, "cloud_brief": cp}
        return {"id": rid, "ok": False, "error": "unknown_action"}
    except Exception as e:  # noqa: BLE001
        return {"id": rid, "ok": False, "error": type(e).__name__ + ":" + str(e)[:200]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="本地桥 stdio JSON-RPC")
    ap.add_argument("--stdio", action="store_true")
    ap.add_argument("--action", default="")
    ap.add_argument("--user", default="")
    ap.add_argument("--authorize", action="store_true")
    ap.add_argument("--base", default="")
    a = ap.parse_args(argv)
    global BASE
    if a.base:
        BASE = Path(a.base)
    if a.stdio:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except ValueError:
                print(json.dumps({"ok": False, "error": "bad_json"}, ensure_ascii=False), flush=True)
                continue
            print(json.dumps(handle(req), ensure_ascii=False), flush=True)
        return 0
    if not a.action:
        ap.error("need --stdio or --action")
    req = {"id": "cli", "action": a.action, "user": a.user, "authorize": a.authorize}
    out = handle(req)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
