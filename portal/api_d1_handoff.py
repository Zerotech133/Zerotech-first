# -*- coding: utf-8 -*-
"""D1 交棒回执 API（WO-D1-HANDOFF-20261101；v2 WO-IFS-HANDOFF-20261103）。

POST /api/d1/handoff {action:create|ack,...}
     — create 支持 d1_handoff/v2（带 watch_plans[]），v1 旧请求继续可用；
GET  /api/d1/handoffs?code=...   — 返回 schema_version（v1 旧行归一 v1）+ watch_plans 视图
GET  /api/d1/watch-plans?status= — watch_plan 状态机只读列表（不触发/不执行）

只落回执/只读 plan，不生成订单/不执行、不接实盘（paper-only）。
接线说明：本模块已实现 /api/d1/watch-plans 的只读响应；portal_api.py 主文件
（禁改）需由 Lead 增加一行 dispatch：``elif path == "/api/d1/watch-plans"``。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import ifs_envelope
import ifs_idempotency  # IFS G1 幂等重放（WO-IFS-IDEMP-20261104）

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

MAX_BODY = 64 * 1024
API_SCHEMA_VERSION = "d1_handoff/v2"

# v2 新增的 400 错误码（其余 bad_/missing_ 前缀亦按 400 处理）
BAD_REQUEST_CODES = {
    "bad_body", "missing_code", "missing_target", "missing_action",
    "missing_rationale", "bad_target", "bad_action", "bad_target_action",
    "not_paper", "execution_field_forbidden", "bad_buy_zone",
    "duplicate_receipt", "bad_ack_status", "receipt_not_found",
    "already_acked", "bad_action_name",
    # v2（watch_plans）
    "unsupported_schema", "schema_mismatch", "bad_watch_plans", "bad_watch_plan",
    "bad_plan_id", "duplicate_plan_id", "bad_plan_seat", "bad_plan_status",
    "bad_conditions", "bad_expires_at", "watch_plans_require_watch",
}


def _json(handler, obj, code=200):
    ifs_envelope.send(handler, obj, code, fallback_interface="d1.handoff.create")


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
    bad = (
        code in BAD_REQUEST_CODES
        or code.startswith("bad_")
        or code.startswith("missing_")
    )
    status = 400 if bad else 500
    _json(handler, {"ok": False, "error": code, "message": str(exc)[:300]}, status)


def _handoff_ledger():
    from agent_platform import d1_handoff as H

    return H.ledger_from_env()


def _watch_plan_ledger():
    from agent_platform import watch_plan_consumer as C

    return C.plan_ledger_path()


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
    ledger = _handoff_ledger()
    try:
        from agent_platform import d1_handoff as H
        if action in ("create", "handoff"):
            key, key_error = ifs_idempotency.resolve_key(handler, obj)
            if key_error:
                _json(handler, {"ok": False, "error": key_error,
                                "message": "幂等键非法：Idempotent-Key 需 8-128 位"
                                           "[A-Za-z0-9._:-]，以字母/数字开头"}, 400)
                return
            with ifs_idempotency.replay_guard(
                    "d1.handoff.create", key, handler, obj) as ctx:
                if ctx.replay is not None:
                    if ctx.conflict():
                        _json(handler, {"ok": False, "error": "idempotency_conflict",
                                        "message": "同一 Idempotent-Key 的请求体与首次不一致，"
                                                   "拒绝重复/改写写入"}, 409)
                        return
                    _json(handler, ctx.replayed_payload(), 200)
                    return
                row = H.append_handoff(obj, ledger=ledger)
                result = {
                    "ok": True,
                    "schema_version": H.schema_version(row),
                    "handoff": row,
                    "watch_plans": H.watch_plans_of(row),
                    "summary": H.summary(ledger=ledger),
                    "paper_only": True,
                    "not_a_trade": True,
                }
                ctx.remember(result)
                _json(handler, result, 200)
            return
        if action in ("ack", "acknowledge"):
            row = H.ack_handoff(obj.get("receipt_id"), obj.get("by"),
                                obj.get("status") or "received",
                                obj.get("note") or "", ledger=ledger)
            _json(handler, {
                "ok": True,
                "schema_version": H.schema_version(row),
                "handoff": row,
                "summary": H.summary(ledger=ledger),
                "paper_only": True,
            }, 200)
            return
        _json(handler, {"ok": False, "error": "bad_action_name",
                        "message": "action 必须是 create/ack"}, 400)
    except Exception as exc:  # noqa: BLE001
        _err(handler, exc)


def _watch_plans_payload(status=""):
    from agent_platform import watch_plan_consumer as C

    plans = C.list_plans(path=_watch_plan_ledger(), status=status or None)
    return {
        "ok": True,
        "schema_version": C.SCHEMA,
        "count": len(plans),
        "watch_plans": plans,
        "readonly": True,
        "paper_only": True,
        "executes": False,
    }


def handle_get(handler, qs):
    path = handler.path.split("?", 1)[0]
    from urllib.parse import parse_qs

    q = parse_qs(qs or "")
    if path == "/api/d1/watch-plans":
        status = (q.get("status") or [""])[0].strip().lower()
        try:
            _json(handler, _watch_plans_payload(status), 200)
        except Exception as exc:  # noqa: BLE001
            _err(handler, exc)
        return
    if path != "/api/d1/handoffs":
        _json(handler, {"ok": False, "error": "not_found"}, 404)
        return
    code = (q.get("code") or [""])[0].strip()
    ledger = _handoff_ledger()
    try:
        from agent_platform import d1_handoff as H
        if code:
            _json(handler, {
                "ok": True,
                "schema_version": H.SCHEMA,
                "code": code,
                "latest": H.latest_for(code, ledger=ledger),
                "watch_plans": [p for p in H.watch_plans_from_handoffs(ledger) if p.get("code") == code],
                "summary": H.summary(ledger=ledger),
                "readonly": True,
            }, 200)
            return
        _json(handler, {
            "ok": True,
            "schema_version": H.SCHEMA,
            "summary": H.summary(ledger=ledger),
            "handoffs": H.read_handoffs(ledger)[-100:],
            "watch_plans": H.watch_plans_from_handoffs(ledger),
            "readonly": True,
        }, 200)
    except Exception as exc:  # noqa: BLE001
        _err(handler, exc)
