# -*- coding: utf-8 -*-
"""D1 交棒回执 API（WO-D1-HANDOFF-20261101）。

POST /api/d1/handoff {action:create|ack,...}
GET  /api/d1/handoffs?code=...
只落回执；不生成订单/不执行。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

MAX_BODY = 64 * 1024


def _json(handler, obj, code=200):
    handler._json(obj, code)


def _body(handler):
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        length = 0
    if length > MAX_BODY:
        return None, "body_too_large"
    raw = handler.rfile.read(length) if length else b"{}"
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, "bad_json"
    if not isinstance(obj, dict):
        return None, "bad_body"
    return obj, ""


def _err(handler, exc):
    code = getattr(exc, "code", "") or "handoff_failed"
    status = 400 if code in {
        "bad_body", "missing_code", "missing_target", "missing_action",
        "missing_rationale", "bad_target", "bad_action", "bad_target_action",
        "not_paper", "execution_field_forbidden", "bad_buy_zone",
        "duplicate_receipt", "bad_ack_status", "receipt_not_found",
        "already_acked", "bad_action_name",
    } else 500
    _json(handler, {"ok": False, "error": code, "message": str(exc)[:300]}, status)


def handle_post(handler):
    path = handler.path.split("?", 1)[0]
    if path != "/api/d1/handoff":
        _json(handler, {"ok": False, "error": "not_found"}, 404)
        return
    obj, err = _body(handler)
    if err == "body_too_large":
        _json(handler, {"ok": False, "error": err}, 413)
        return
    if err:
        _json(handler, {"ok": False, "error": err}, 400)
        return
    action = str(obj.get("action_name") or obj.get("op") or "create").lower()
    try:
        from agent_platform import d1_handoff as H
        if action in ("create", "handoff"):
            row = H.append_handoff(obj)
            _json(handler, {"ok": True, "handoff": row,
                            "summary": H.summary(), "paper_only": True,
                            "not_a_trade": True}, 200)
            return
        if action in ("ack", "acknowledge"):
            row = H.ack_handoff(obj.get("receipt_id"), obj.get("by"),
                                obj.get("status") or "received",
                                obj.get("note") or "")
            _json(handler, {"ok": True, "handoff": row,
                            "summary": H.summary(), "paper_only": True}, 200)
            return
        _json(handler, {"ok": False, "error": "bad_action_name",
                        "message": "action 必须是 create/ack"}, 400)
    except Exception as exc:  # noqa: BLE001
        _err(handler, exc)


def handle_get(handler, qs):
    path = handler.path.split("?", 1)[0]
    if path != "/api/d1/handoffs":
        _json(handler, {"ok": False, "error": "not_found"}, 404)
        return
    from urllib.parse import parse_qs
    q = parse_qs(qs or "")
    code = (q.get("code") or [""])[0].strip()
    try:
        from agent_platform import d1_handoff as H
        if code:
            _json(handler, {"ok": True, "code": code,
                            "latest": H.latest_for(code),
                            "summary": H.summary(), "readonly": True}, 200)
            return
        _json(handler, {"ok": True, "summary": H.summary(),
                        "handoffs": H.read_handoffs()[-100:],
                        "readonly": True}, 200)
    except Exception as exc:  # noqa: BLE001
        _err(handler, exc)
