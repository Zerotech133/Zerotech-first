# -*- coding: utf-8 -*-
"""D1 投研会 API（WO-D1-MEETING-20261101，2026-11-01）。

路由：
  POST /api/d1/meeting  {action: create|vote|finalize, ...}
  GET  /api/d1/meeting?meeting_id=...  （会议详情；无 id 时 list+summary）
边界：只落会议纪要 / D1 最终表决；不自动执行、不下单。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 8091 以脚本方式启动时 sys.path 只有 portal/；d1 模块在仓库根。
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import json

import ifs_envelope
import ifs_idempotency  # IFS G1 幂等重放（WO-IFS-IDEMP-20261104）

MAX_BODY = 64 * 1024
ERROR_CODES = {
    "missing_code", "bad_creator", "bad_meeting_id", "duplicate_meeting",
    "meeting_not_found", "meeting_corrupt", "bad_seat", "meeting_closed",
    "duplicate_vote", "bad_tier", "bad_confidence", "bad_rationale",
    "bad_evidence", "quorum_not_reached", "bad_decider",
}


def _json(handler, obj, code=200):
    ifs_envelope.send(handler, obj, code, fallback_interface="d1.meeting.get")


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
    handler._ifs_method = "POST"  # IFS F1b：envelope source.interface_id 需区分 get/create
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
            key, key_error = ifs_idempotency.resolve_key(handler, obj)
            if key_error:
                _json(handler, {"ok": False, "error": key_error,
                                "message": "幂等键非法：Idempotent-Key 需 8-128 位"
                                           "[A-Za-z0-9._:-]，以字母/数字开头"}, 400)
                return
            with ifs_idempotency.replay_guard(
                    "d1.meeting.create", key, handler, obj) as ctx:
                if ctx.replay is not None:
                    if ctx.conflict():
                        _json(handler, {"ok": False, "error": "idempotency_conflict",
                                        "message": "同一 Idempotent-Key 的请求体与首次不一致，"
                                                   "拒绝重复/改写写入"}, 409)
                        return
                    _json(handler, ctx.replayed_payload(), 200)
                    return
                doc = M.create_meeting(
                    obj.get("code"), topic=obj.get("topic") or "",
                    asof=obj.get("asof") or "",
                    agenda=obj.get("agenda") or None,
                    created_by=obj.get("created_by") or M.CHAIR,
                )
                result = {"ok": True, "meeting": doc,
                          "paper_only": True, "not_a_trade": True}
                ctx.remember(result)
                _json(handler, result, 200)
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
    handler._ifs_method = "GET"  # IFS F1b：envelope source.interface_id 需区分 get/create
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
