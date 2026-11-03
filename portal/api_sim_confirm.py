# -*- coding: utf-8 -*-
"""门户：模拟交易人工确认 API v2（IFS v1 F4；WO-IFS-PAPER-20261103）。

**唯一 A 路径**的 HTTP 入口（复用既有 trading_bot_flow，不重写执行链）::

    POST /api/sim/confirm          approve/reject + 四 ID 上游字段（可选补齐）
    GET  /api/sim/confirmations    pending/已裁决列表（v2 schema 视图）

本模块由 Lead 接线进 ``portal_api.py``（替换旧 ``import sim_confirm``；本单
不写 portal_api.py 主文件）：

- POST 分发行：``api_sim_confirm.handle_post(self)``；
- GET 分发行：``self._json(api_sim_confirm.list_payload(status))``。

legacy B/C 入口（``/api/paper_order``、``/api/paper_direct_open``）不接 D1：
接线时用 ``legacy_marker(path)`` / ``annotate_legacy(payload, path)`` /
``entry_headers(path)`` 给响应打 “legacy” 标（口径见
``agent_platform.paper.entry_policy``）。

边界：只读/写 paper 模拟数据（confirmation + receipt），不下单、不接实盘、
不启动服务、不迁移两账；``paper_only=true`` / ``trading_permitted=false``。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from agent_platform import trading_bot_flow as flow  # noqa: E402
from agent_platform.paper import entry_policy, trade_receipt  # noqa: E402

SCHEMA = "sim_confirm_api/v2"
ENDPOINT = "/api/sim/confirm"
LIST_ENDPOINT = "/api/sim/confirmations"
MAX_BODY = 64 * 1024
ACTIONS = ("approve", "reject")


def _json(handler, obj, code=200):
    handler._json(obj, code)


def _body(handler):
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        length = 0
    if length < 0:
        return None, "bad_length"
    if length > MAX_BODY:
        return None, "body_too_large"
    raw = handler.rfile.read(length) if length else b"{}"
    try:
        obj = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, ValueError):
        return None, "bad_json"
    if not isinstance(obj, dict):
        return None, "bad_body"
    return obj, ""


def _source_ids(body):
    """从请求体取四 ID 上游字段（缺省 None=不改写；空串视为未提供）。"""
    out = {}
    for key in ("source_decision_id", "source_handoff_id",
                "source_watch_plan_id"):
        value = str(body.get(key) or "").strip()
        if value:
            out[key] = value
    return out


def _item_payload(rec):
    """单条 confirmation → 只读响应（四 ID 链 + entry 口径 + 回执摘要）。"""
    chain = rec.get("ids") if isinstance(rec.get("ids"), dict) else None
    if not chain:
        chain = dict(flow.id_chain(rec))
        execution = rec.get("execution") or {}
        if execution.get("ids"):
            chain = dict(execution["ids"])
    return {
        "confirmation_id": rec.get("confirmation_id"),
        "status": rec.get("status"),
        "symbol": rec.get("symbol"),
        "action": rec.get("action"),
        "trade_id": rec.get("trade_id"),
        "ids": chain,
        "schema": rec.get("schema"),
        "schema_version": rec.get("schema_version"),
        "entry_path": rec.get("entry_path") or entry_policy.UNIQUE_A_PATH,
        "paper_only": True,
        "trading_permitted": False,
        "mode": "paper",
        "decided_by": rec.get("decided_by"),
        "decided_at": rec.get("decided_at"),
        "gate": rec.get("gate"),
        "execution": rec.get("execution"),
    }


def _response(rec):
    """POST /api/sim/confirm 的响应体（入口 A + 四 ID 链回写）。"""
    item = _item_payload(rec)
    payload = {
        "ok": True,
        "schema": SCHEMA,
        "mode": "paper",
        "paper_only": True,
        "trading_permitted": False,
        "executed": rec.get("status") == "executed",
        "trade_id": item["trade_id"],
        "ids": item["ids"],
        "item": item,
        "confirmation": item,
    }
    return entry_policy.annotate(payload, ENDPOINT)


def handle_post(handler):
    """POST /api/sim/confirm：approve/reject（只读执行入口 A）。"""
    path = handler.path.split("?", 1)[0].rstrip("/") or "/"
    if path != ENDPOINT:
        _json(handler, {"ok": False, "error": "not_found"}, 404)
        return
    body, err = _body(handler)
    if err == "body_too_large":
        _json(handler, _err_payload("body_too_large"), 413)
        return
    if err:
        _json(handler, _err_payload(err), 400)
        return
    cid = str(body.get("confirmation_id") or "").strip()
    action = str(body.get("action") or "").strip().lower()
    if not cid or action not in ACTIONS:
        _json(handler, _err_payload(
            "confirmation_id/action 不合法（action 只认 approve/reject）"), 400)
        return
    try:
        rec = flow.decide(
            cid, action,
            by=str(body.get("by") or "老田"),
            note=str(body.get("note") or ""),
            price=body.get("price"),
            profile=body.get("profile"),
            **(_source_ids(body)),
        )
    except (OSError, ValueError, KeyError) as exc:
        _json(handler, _err_payload(str(exc)), 400)
        return
    _json(handler, _response(rec), 200)


def _err_payload(message):
    payload = {
        "ok": False,
        "schema": SCHEMA,
        "error": message,
        "mode": "paper",
        "paper_only": True,
        "trading_permitted": False,
    }
    return entry_policy.annotate(payload, ENDPOINT)


def list_payload(status="pending", data_dir=None):
    """GET /api/sim/confirmations：列表（v2 视图 + entry 口径 + 回执摘要）。"""
    items = [_item_payload(rec) for rec in
             flow.list_confirmations(status=status, data_dir=data_dir)]
    payload = {
        "ok": True,
        "schema": SCHEMA,
        "mode": "paper",
        "paper_only": True,
        "trading_permitted": False,
        "status": status,
        "count": len(items),
        "items": items,
        "receipts": flow.receipt_summary(data_dir),
    }
    return entry_policy.annotate(payload, ENDPOINT)


def handle_get(handler, qs=""):
    """GET /api/sim/confirmations?status=pending（供 Lead 可选接线）。"""
    from urllib.parse import parse_qs

    path = handler.path.split("?", 1)[0].rstrip("/") or "/"
    if path != LIST_ENDPOINT:
        _json(handler, {"ok": False, "error": "not_found"}, 404)
        return
    query = parse_qs(qs or "")
    status = (query.get("status") or ["pending"])[0] or "pending"
    _json(handler, list_payload(status), 200)


def receipts_payload(trade_id):
    """只读：按 trade_id 取回执（不存在 → None；不造数）。"""
    row = trade_receipt.find_receipt(trade_id=str(trade_id or ""))
    return entry_policy.annotate({
        "ok": row is not None,
        "schema": "paper_trade_receipt/v1",
        "receipt": row,
        "readonly": True,
        "paper_only": True,
        "trading_permitted": False,
    }, ENDPOINT)


# ── legacy B/C 入口标注（Lead 接线用；不改旧端点实现）────────────────
def legacy_marker(path):
    """旧入口 legacy 标注（非 legacy → None）。"""
    return entry_policy.legacy_marker(path)


def annotate_legacy(payload, path):
    """给旧入口响应附加 legacy 标注（不覆盖业务字段）。"""
    return entry_policy.annotate(payload, path)


def entry_headers(path):
    """HTTP 头：A → ``X-Paper-Entry: A``；legacy → ``legacy``；未知 → {}。"""
    return entry_policy.entry_headers(path)


def describe():
    """API 元信息（入口口径；供接线/审计/页面）。"""
    return {
        "schema": SCHEMA,
        "endpoint_post": ENDPOINT,
        "endpoint_list": LIST_ENDPOINT,
        "entry": entry_policy.describe(),
        "paper_only": True,
        "trading_permitted": False,
        "real_trading": False,
    }


__all__ = [
    "SCHEMA",
    "ENDPOINT",
    "LIST_ENDPOINT",
    "MAX_BODY",
    "handle_post",
    "handle_get",
    "list_payload",
    "receipts_payload",
    "legacy_marker",
    "annotate_legacy",
    "entry_headers",
    "describe",
]
