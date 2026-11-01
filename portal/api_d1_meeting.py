# -*- coding: utf-8 -*-
"""D1 投研会 API（WO-D1-MEETING-20261101，2026-11-01）。

路由：
  POST /api/d1/meeting  {action: create|vote|finalize, ...}
  GET  /api/d1/meeting?meeting_id=...  （会议详情；无 id 时 list+summary）
边界：只落会议纪要 / D1 最终表决；不自动执行、不下单。
"""
from __future__ import annotations

import json

MAX_BODY = 64 * 1024
ERROR_CODES = {
    "missing_code", "bad_creator", "bad_meeting_id", "duplicate_meeting",
    "meeting_not_found", "meeting_corrupt", "bad_seat", "meeting_closed",
    "duplicate_vote", "bad_tier", "bad_confidence", "bad_rationale",
    "bad_evidence", "quorum_not_reached", "bad_decider",
}


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


def _error(handler, exc):
    code = getattr(exc, "code", "") or "meeting_failed"
    status = 400 if code in ERROR_CODES else 500
    _json(handler, {"ok": False, "error": code,
                    "message": str(exc)[:300]}, status)


def handle_post(handler):
    path = handler.path.split("?", 1)[0]
    if path != "/api/d1/meeting":
        _json(handler, {"ok": False, "error": "not_found"}, 404)
        return
    obj, err = _body(handler)
    if err == "body_too_large":
        _json(handler, {"ok": False, "error": err}, 413)
        return
    if err:
        _json(handler, {"ok": False, "error": err}, 400)
        return
    action = str(obj.get("action") or "").strip().lower()
    try:
        from agent_platform import d1_meeting as M
        if action == "create":
            doc = M.create_meeting(
                obj.get("code"), topic=obj.get("topic") or "",
                asof=obj.get("asof") or "",
                agenda=obj.get("agenda") or None,
                created_by=obj.get("created_by") or M.CHAIR,
            )
            _json(handler, {"ok": True, "meeting": doc,
                            "paper_only": True, "not_a_trade": True}, 200)
            return
        mid = str(obj.get("meeting_id") or "").strip()
        if action == "vote":
            doc = M.submit_vote(
                mid, obj.get("seat"), obj.get("tier"),
                obj.get("confidence"), obj.get("rationale"),
                evidence=obj.get("evidence"),
            )
            _json(handler, {"ok": True, "meeting": doc,
                            "quorum": M.can_finalize(doc),
                            "paper_only": True}, 200)
            return
        if action == "finalize":
            doc = M.finalize(
                mid, obj.get("tier"), obj.get("confidence"),
                obj.get("rationale"), evidence=obj.get("evidence"),
                dissent=obj.get("dissent"),
                decided_by=obj.get("decided_by") or M.CHAIR,
            )
            _json(handler, {"ok": True, "meeting": doc,
                            "decision_candidate": doc.get("decision_candidate"),
                            "paper_only": True, "not_a_trade": True}, 200)
            return
        _json(handler, {"ok": False, "error": "bad_action",
                        "message": "action 必须是 create/vote/finalize"}, 400)
    except Exception as exc:  # noqa: BLE001
        _error(handler, exc)


def handle_get(handler, qs):
    path = handler.path.split("?", 1)[0]
    if path != "/api/d1/meeting":
        _json(handler, {"ok": False, "error": "not_found"}, 404)
        return
    from urllib.parse import parse_qs
    q = parse_qs(qs or "")
    mid = (q.get("meeting_id") or [""])[0]
    try:
        from agent_platform import d1_meeting as M
        if mid:
            doc = M.get_meeting(mid)
            _json(handler, {"ok": True, "meeting": doc,
                            "quorum": M.can_finalize(doc)}, 200)
        else:
            _json(handler, {"ok": True, "summary": M.summary(),
                            "meetings": M.list_meetings(limit=30)}, 200)
    except Exception as exc:  # noqa: BLE001
        _error(handler, exc)
