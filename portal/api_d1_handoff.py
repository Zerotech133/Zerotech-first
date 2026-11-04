# -*- coding: utf-8 -*-
"""D1 交棒回执 API（WO-D1-HANDOFF-20261101；v2 WO-IFS-HANDOFF-20261103）。

POST /api/d1/handoff {action:create|ack,...}
     — create 支持 d1_handoff/v2（带 watch_plans[]），v1 旧请求继续可用；
GET  /api/d1/handoffs?code=...   — 返回 schema_version（v1 旧行归一 v1）+ watch_plans 视图
GET  /api/d1/watch-plans?status= — watch_plan 状态机只读列表（不触发/不执行）；
     新增 ``engine`` 块（读 watch_engine/status.json，缺失/坏文件→engine_state=unavailable，
     不 500）、``status`` 过滤（armed/triggered/expired/cancelled，非法→400）、
     armed 条目附 ``expires_in_s``/``stale``（WO-WATCH-API-20261104）。

只落回执/只读 plan，不生成订单/不执行、不接实盘（paper-only）。
接线说明：本模块已实现 /api/d1/watch-plans 的只读响应；portal_api.py 主文件
（禁改）需由 Lead 增加一行 dispatch：``elif path == "/api/d1/watch-plans"``。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import ifs_envelope
import ifs_idempotency  # IFS G1 幂等重放（WO-IFS-IDEMP-20261104）

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

MAX_BODY = 64 * 1024
API_SCHEMA_VERSION = "d1_handoff/v2"

# ── watch 引擎状态可观测面（WO-WATCH-API-20261104）──────────────────
#: watch_engine/status.json 路径（只读；env 覆盖供测试/多环境）
WATCH_ENGINE_STATUS_ENV = "WATCH_ENGINE_STATUS_PATH"
DEFAULT_WATCH_ENGINE_STATUS = (_REPO / "agent_platform" / "data"
                               / "watch_engine" / "status.json")
#: /api/d1/watch-plans?status= 允许的过滤值（consumer 落盘状态；非法→400）
WATCH_PLAN_STATUSES = ("armed", "triggered", "expired", "cancelled")
#: 引擎状态白名单透出字段（仅标量；不返回 token/凭据）
ENGINE_SAFE_KEYS = (
    "state", "engine_state", "status", "mode", "version", "pid",
    "started_at", "last_tick_at", "heartbeat_at", "last_heartbeat_at",
    "tick_count", "ticks", "armed_count", "plans_armed", "plan_count",
    "interval_s", "updated_at", "generated_at", "note", "reason",
)
#: age_s 超过该阈值视为引擎心跳 stale（默认 180s）
ENGINE_STALE_AFTER_S = 180
#: 引擎状态文件最大读取体积（防御坏文件/超大文件）
ENGINE_STATUS_MAX_BYTES = 256 * 1024

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


# ── watch 引擎状态 / armed 时效（只读可观测；WO-WATCH-API-20261104）──
def _now_dt():
    return datetime.now(timezone.utc).astimezone()


def _now_iso():
    return _now_dt().isoformat(timespec="seconds")


def _parse_ts(value):
    """ISO8601 → aware datetime；非法/缺失返回 None（不猜）。"""
    text = str(value or "").strip()
    if not text:
        return None
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.astimezone()


def _engine_status_path():
    override = str(os.environ.get(WATCH_ENGINE_STATUS_ENV) or "").strip()
    return Path(override) if override else DEFAULT_WATCH_ENGINE_STATUS


def _engine_rel(path):
    try:
        return str(Path(path).resolve().relative_to(_REPO.resolve())).replace("\\", "/")
    except (ValueError, OSError):
        return str(path)


def _watch_engine_status(now=None):
    """读取 watch_engine/status.json → 只读 engine 块。

    缺失 / 不可读 / 坏 JSON / 顶层非 object / 超限 → ``engine_state=unavailable``
    （HTTP 仍 200，不 500）；只透出白名单标量字段，不返回 token/凭据。
    """
    now = now or _now_dt()
    path = _engine_status_path()
    block = {
        "engine_state": "unavailable",
        "source": _engine_rel(path),
        "checked_at": now.isoformat(timespec="seconds"),
        "status_file": {"exists": False, "bytes": 0, "mtime": None},
        "stale": True,
        "age_s": None,
        "stale_after_s": ENGINE_STALE_AFTER_S,
    }
    try:
        stat = path.stat()
    except OSError:
        block["note"] = "watch_engine/status.json 缺失（引擎未运行/未接线；不伪造）"
        return block
    block["status_file"] = {
        "exists": True,
        "bytes": stat.st_size,
        "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        .astimezone().isoformat(timespec="seconds"),
    }
    if stat.st_size > ENGINE_STATUS_MAX_BYTES:
        block["note"] = "status.json 超过 %d bytes，拒绝读取（不 500）" % ENGINE_STATUS_MAX_BYTES
        return block
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        block["note"] = "status.json 不可读/非法 JSON：%s" % str(exc)[:200]
        return block
    if not isinstance(data, dict):
        block["note"] = "status.json 顶层不是 object（不伪造）"
        return block
    state = ""
    for key in ("engine_state", "state", "status"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            state = value.strip().lower()
            break
    block["engine_state"] = state or "unknown"
    for key in ENGINE_SAFE_KEYS:
        if key in ("state", "engine_state", "status"):
            continue  # 已归一为 engine_state，避免重复键
        value = data.get(key)
        if not isinstance(value, (str, int, float, bool)):
            continue  # 只透出标量；忽略嵌套对象/数组（防夹带敏感结构）
        if isinstance(value, str) and len(value) > 200:
            continue
        block[key] = value
    last_ts = None
    for key in ("last_tick_at", "heartbeat_at", "last_heartbeat_at",
                "updated_at", "generated_at", "started_at"):
        last_ts = _parse_ts(data.get(key))
        if last_ts is not None:
            break
    if last_ts is None:
        block["note"] = "status.json 缺可解析心跳时间（last_tick_at/heartbeat_at）"
        return block
    age_s = max(0, int((now - last_ts).total_seconds()))
    block["age_s"] = age_s
    block["stale"] = age_s > ENGINE_STALE_AFTER_S
    return block


def _plan_view(plan, now):
    """armed plan 附加 expires_in_s（可负=已过期未触发）与 stale；不动其它状态。"""
    if not isinstance(plan, dict):
        return plan
    view = dict(plan)
    if str(view.get("status") or "").lower() != "armed":
        return view
    expires = _parse_ts(view.get("expires_at"))
    if expires is None:
        view["expires_in_s"] = None
        view["stale"] = True  # 到期时间不可解析 → 不可信，按 stale 展示
        return view
    expires_in_s = int((expires - now).total_seconds())
    view["expires_in_s"] = expires_in_s
    view["stale"] = expires_in_s <= 0
    return view


def _watch_plans_payload(status=""):
    from agent_platform import watch_plan_consumer as C

    now = _now_dt()
    plans = C.list_plans(path=_watch_plan_ledger(), status=status or None)
    views = [_plan_view(plan, now) for plan in plans]
    return {
        "ok": True,
        "schema_version": C.SCHEMA,
        "count": len(views),
        "watch_plans": views,
        "readonly": True,
        "paper_only": True,
        "executes": False,
        # 新增（只加不改）：过滤回显 + 引擎状态块
        "status_filter": (str(status).lower() or None) if status else None,
        "engine": _watch_engine_status(now),
    }


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


def handle_get(handler, qs):
    path = handler.path.split("?", 1)[0]
    from urllib.parse import parse_qs

    q = parse_qs(qs or "")
    if path == "/api/d1/watch-plans":
        status = (q.get("status") or [""])[0].strip().lower()
        if status and status not in WATCH_PLAN_STATUSES:
            _json(handler, {
                "ok": False,
                "error": "bad_status",
                "message": "status 仅支持：%s（缺省=全部）"
                           % "/".join(WATCH_PLAN_STATUSES),
            }, 400)
            return
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
