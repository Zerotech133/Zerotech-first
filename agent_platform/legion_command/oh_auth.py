# -*- coding: utf-8 -*-
"""C1：8796 的 OH 写/执行入口准入（门户 session 或能力令牌）。

- 8771 保持 key-gated loopback（Bearer key + 127.0.0.1 publish）；
- 8796 的 start/chat/respond/interrupt/sessions-delete/orchestrate/commit
  需要：有效门户 zb_session 或 X-ZB-Cap(scope=run:execute)。
- 读接口（health/tasks/sessions/status/stream/diff）保持只读开放。
- OH_BROKER_AUTH=0 可整体关闭（测试/回滚）。
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

ENV = "OH_BROKER_AUTH"
PORTAL = "http://127.0.0.1:8091"


def enabled() -> bool:
    return str(os.environ.get(ENV, "1")).strip().lower() not in ("0", "off", "false", "no")


def _cookie(headers: dict, name: str) -> str:
    for part in str(headers.get("Cookie") or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip() == name:
                return v.strip()
    return ""


def _portal_ok(headers: dict) -> bool:
    sid = _cookie(headers, "zb_session")
    if not sid:
        return False
    try:
        req = urllib.request.Request(
            PORTAL + "/api/bff/identity/me",
            headers={"Cookie": "zb_session=" + sid},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=2) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        sess = data.get("session") or data
        return bool(sess)
    except (OSError, ValueError, urllib.error.URLError):
        return False


def _cap_token():
    import importlib.util as _ilu
    root = Path(__file__).resolve().parents[2]
    p = root / "agent_platform" / "legion_ide" / "cap_token.py"
    spec = _ilu.spec_from_file_location("oh_cap_token", p)
    m = _ilu.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _cap_ok(headers: dict) -> bool:
    token = str(headers.get("X-ZB-Cap") or "").strip() or _cookie(headers, "zbcap")
    if not token:
        return False
    try:
        m = _cap_token()
        if not m.enabled():
            return False
        res = m.verify(token, required_scope="run:execute", task="")
        if res.get("ok"):
            return True
        # 兼容 ide:exec: 前缀 token（高权限本地任务）
        for scope in ("ide:exec",):
            if m.verify(token, required_scope=scope, task="").get("ok"):
                return True
        return False
    except Exception:
        return False


def authorize(headers: dict):
    """返回 None=放行；否则返回 err dict。"""
    if not enabled():
        return None
    if _portal_ok(headers) or _cap_ok(headers):
        return None
    return {"ok": False, "error": "oh_auth_required",
            "message": "OH 写/执行需要门户登录 session 或 run:execute 能力令牌"}
