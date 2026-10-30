# -*- coding: utf-8 -*-
"""门户：模拟交易人工确认（WO-BOT-CONFIRM）。

只做人工确认/拒绝的薄层；执行仍由 agent_platform.trading_bot_flow 统一负责。
"""
from __future__ import annotations

import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
from agent_platform import trading_bot_flow as flow  # noqa: E402


def list_payload(status="pending"):
    items = flow.list_confirmations(status)
    return {"ok": True, "mode": "paper", "trading_permitted": False,
            "status": status, "count": len(items), "items": items}


def _body(handler):
    try:
        n = int(handler.headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        n = 0
    raw = handler.rfile.read(n) if n > 0 else b"{}"
    try:
        return json.loads(raw.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        return {}


def handle_post(handler):
    body = _body(handler)
    cid = str(body.get("confirmation_id") or "")
    action = str(body.get("action") or "").lower()
    if not cid or action not in ("approve", "reject"):
        handler._json({"ok": False, "error": "confirmation_id/action 不合法"}, 400)
        return
    try:
        rec = flow.decide(cid, action, by=body.get("by") or "老田",
                          note=body.get("note") or "", price=body.get("price"),
                          profile=body.get("profile"))
    except (OSError, ValueError, KeyError) as exc:
        handler._json({"ok": False, "error": str(exc)}, 400)
        return
    handler._json({"ok": True, "mode": "paper", "item": rec,
                   "trading_permitted": False})
