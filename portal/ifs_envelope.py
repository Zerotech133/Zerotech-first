# -*- coding: utf-8 -*-
r"""IFS F1b：D1 读接口统一 envelope / request_id 接线（WO-IFS-ENVELOPE-20261104）。

复用 ``agent_platform/contracts/ifs.py`` 的 F1 适配器，把 D1 只读/只写接口的响应
统一叠一层 IFS-03 信封，**同时保留旧顶层字段**（兼容期，前端/旧测试零改动）：

成功响应（叠加字段，旧字段原样保留）::

    {
      "ok": true,
      "schema_version": "d1_handoff/v2",
      "request_id": "req-<32hex>",
      "source": {"interface_id": "d1.handoff.list", "source_of_truth": "...",
                 "source_mode": "ledger", "stale": false},
      "data": {...原 payload 副本...},
      "warnings": [],
      ...原顶层字段（handoff/handoffs/summary/watch_plans/latest/... 全部保留）...
    }

失败响应（兼容期双形态，HTTP 状态沿用原状态映射）::

    {
      "ok": false,
      "schema_version": "error/v1",
      "request_id": "req-<32hex>",
      "error": "<旧字符串错误码，保留兼容>",
      "error_detail": {"code": "invalid_parameter", "message": "...",
                       "retryable": false,
                       "details": {"legacy_code": "bad_target_action", "http_status": 400}},
      "warnings": [],
      ...原顶层字段...
    }

> 双形态说明：IFS-03 定稿要求 ``error`` 为对象，但 D1 旧页面（如
> ``acad1_pool.html``）与既有契约测试依赖 ``error`` 字符串；F1b 采用「旧 ``error``
> 字符串 + ``error_detail`` IFS 对象」兼容期形态，待前端迁移后（F1c）再把
> ``error`` 切为对象（破坏性变更走 v2 + 90 天双版本，见 IFS-09）。

``X-Request-Id``：``send()`` 把 request_id 记录到 ``handler._ifs_request_id``，
``portal_api.Handler._json`` 统一回写响应头（header 与 body.request_id 一致）。

边界：**只做响应序列化**，不改业务逻辑；不写盘、不联网、不启动服务、不写生产数据。
不支持/未登记的路径原样透传给 ``handler._json``（不接管其它接口）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agent_platform.contracts import ifs  # noqa: E402

SCHEMA_VERSION = ifs.ERROR_SCHEMA_VERSION
ERROR_SCHEMA_VERSION = ifs.ERROR_SCHEMA_VERSION
REQUEST_ID_PATTERN = ifs.REQUEST_ID_PATTERN

#: D1 读接口契约映射（与 docs/standards/INTERFACE_REGISTRY_v1.yaml 对齐，测试校验）
INTERFACES: Dict[str, Dict[str, str]] = {
    "d1.research.get": {
        "path": "/api/d1/research",
        "schema_version": "d1_research/v1",
        "source_of_truth": "agent_platform/data/pool_review/ + agent_platform/data/shadow/shadow_ledger.jsonl",
        "source_mode": "aggregate",
    },
    "d1.decision.create": {
        "path": "/api/d1/decision",
        "schema_version": "d1_decision/v1",
        "source_of_truth": "agent_platform/data/d1_decisions.jsonl",
        "source_mode": "ledger",
    },
    "d1.decision.list": {
        "path": "/api/d1/decisions",
        "schema_version": "d1_decision/v1",
        "source_of_truth": "agent_platform/data/d1_decisions.jsonl",
        "source_mode": "ledger",
    },
    "d1.meeting.get": {
        "path": "/api/d1/meeting",
        "schema_version": "d1_meeting/v1",
        "source_of_truth": "agent_platform/data/meetings/d1/",
        "source_mode": "ledger",
    },
    "d1.meeting.create": {
        "path": "/api/d1/meeting",
        "schema_version": "d1_meeting/v1",
        "source_of_truth": "agent_platform/data/meetings/d1/",
        "source_mode": "ledger",
    },
    "d1.handoff.create": {
        "path": "/api/d1/handoff",
        "schema_version": "d1_handoff/v2",
        "source_of_truth": "agent_platform/data/d1_handoffs.jsonl",
        "source_mode": "ledger",
    },
    "d1.handoff.list": {
        "path": "/api/d1/handoffs",
        "schema_version": "d1_handoff/v2",
        "source_of_truth": "agent_platform/data/d1_handoffs.jsonl",
        "source_mode": "ledger",
    },
    "d1.watch_plans.list": {
        "path": "/api/d1/watch-plans",
        "schema_version": "watch_plan/v1",
        "source_of_truth": "agent_platform/data/watch_plans.jsonl",
        "source_mode": "ledger",
    },
    "training.episode.get": {
        "path": "/api/d1/training/episodes",
        "schema_version": "training/v1",
        "source_of_truth": "agent_platform/data/training/episodes.jsonl",
        "source_mode": "ledger",
    },
}

#: F1d 本地扩展接口（registry 暂无 war-room 条目，待登记；不混入 INTERFACES 以保持
#: 「INTERFACES == registry 已登记接口」的 F1b 契约测试不变）
LOCAL_INTERFACES: Dict[str, Dict[str, str]] = {
    "war_room.snapshot.get": {
        "path": "/api/war-room/snapshot",
        "schema_version": "war_room/v1",
        "source_of_truth": "portal/war_room_data.py 聚合（pool_data/d1_data/paper_data/sim_confirm + watch-admin 快照）",
        "source_mode": "aggregate",
    },
}

#: 运行时全量映射（registry 已登记 + 本地扩展）
INTERFACE_META: Dict[str, Dict[str, str]] = {}
INTERFACE_META.update(INTERFACES)
INTERFACE_META.update(LOCAL_INTERFACES)

_PATH_TO_INTERFACE = {
    meta["path"]: interface_id
    for interface_id, meta in INTERFACE_META.items()
    if interface_id not in ("d1.meeting.create", "d1.meeting.get")
}
_TRAINING_DETAIL_PREFIX = "/api/d1/training/episodes/"

#: 旧错误码 → IFS 错误码（精确命中优先；随后按前缀/HTTP 兜底）
_LEGACY_CODE_MAP = {
    "bad_json": "bad_body",
    "body_too_large": "invalid_request",
    "not_found": "not_found",
    "episode_not_found": "not_found",
    "meeting_not_found": "not_found",
    "receipt_not_found": "not_found",
    "unauthorized": "unauthorized",
    "forbidden": "forbidden",
    "not_paper": "not_paper",
    "execution_field_forbidden": "execution_field_forbidden",
    "data_unavailable": "data_unavailable",
    "war_room_snapshot_failed": "internal_error",
    "bad_idempotency_key": "invalid_parameter",
    "idempotency_conflict": "conflict",
}

_REQUEST_ID_RE = re.compile(REQUEST_ID_PATTERN)


# ── 基础工具 ──────────────────────────────────────────────────────────
def request_id_for(handler: Any) -> str:
    """取请求 request_id：同一请求内稳定（幂等重放需首个 request_id 可追）。

    已记录（``handler._ifs_request_id`` 合法）→ 直接复用；否则合法 X-Request-Id
    沿用、缺失/非法则生成 ``req-<32hex>``，并记录到 handler（响应头回写用）。
    """
    existing = getattr(handler, "_ifs_request_id", "")
    if isinstance(existing, str) and _REQUEST_ID_RE.match(existing):
        return existing
    headers = getattr(handler, "headers", None) or {}
    raw = ""
    try:
        raw = str(headers.get("X-Request-Id") or headers.get("x-request-id") or "").strip()
    except (AttributeError, TypeError):
        raw = ""
    request_id = raw if _REQUEST_ID_RE.match(raw) else ifs.new_request_id()
    try:
        handler._ifs_request_id = request_id
    except (AttributeError, TypeError):
        pass
    return request_id


def interface_for_path(
    path: str,
    method: Optional[str] = None,
    fallback: Optional[str] = None,
) -> Optional[str]:
    """路径（+方法）→ interface_id；未登记返回 fallback（可为 None）。"""
    clean = str(path or "").split("?", 1)[0].rstrip("/")
    if clean == "/api/d1/meeting":
        return (
            "d1.meeting.create"
            if str(method or "").upper() == "POST"
            else "d1.meeting.get"
        )
    if clean.startswith(_TRAINING_DETAIL_PREFIX):
        return "training.episode.get"
    return _PATH_TO_INTERFACE.get(clean, fallback)


def ifs_error_code(legacy_code: str, http_status: int = 400) -> str:
    """旧错误码 → IFS 登记错误码（closed-on-failure，未知按 HTTP 兜底）。"""
    code = str(legacy_code or "").strip()
    if code in ifs.ERROR_CODES:
        return code
    if code in _LEGACY_CODE_MAP:
        return _LEGACY_CODE_MAP[code]
    if ":" in code:
        head = code.split(":", 1)[0].strip()
        if head in ifs.ERROR_CODES:
            return head
    if code.startswith(("missing_", "bad_", "invalid_")):
        return "invalid_parameter"
    if code.startswith("duplicate_"):
        return "duplicate"
    if code.endswith("_conflict"):
        return "conflict"
    if code.endswith(("_failed", "_error", "_corrupt", "_unavailable")) and code != "data_unavailable":
        return "internal_error" if http_status >= 500 else "invalid_parameter"
    if http_status == 404:
        return "not_found"
    if http_status == 401:
        return "unauthorized"
    if http_status == 403:
        return "forbidden"
    if http_status == 413:
        return "invalid_request"
    if http_status == 429:
        return "rate_limited"
    if http_status >= 500:
        return "internal_error"
    return "invalid_parameter"


def _source_ids(interface_id: str, legacy: Mapping[str, Any]) -> Dict[str, str]:
    """从响应体提取可用的 ID chain 字段（缺失不猜）。"""
    ids: Dict[str, str] = {}
    decision = legacy.get("decision")
    if isinstance(decision, Mapping) and decision.get("decision_id"):
        ids["decision_id"] = str(decision["decision_id"])
    latest = legacy.get("latest")
    if isinstance(latest, Mapping) and latest.get("decision_id"):
        ids["decision_id"] = str(latest["decision_id"])
    handoff = legacy.get("handoff")
    if isinstance(handoff, Mapping):
        handoff_id = handoff.get("handoff_id") or handoff.get("receipt_id")
        if handoff_id:
            ids["handoff_id"] = str(handoff_id)
        if handoff.get("decision_id"):
            ids["decision_id"] = str(handoff["decision_id"])
    episode = legacy.get("episode")
    if isinstance(episode, Mapping) and episode.get("training_episode_id"):
        ids["training_episode_id"] = str(episode["training_episode_id"])
    if legacy.get("pool_id"):
        ids["pool_id"] = str(legacy["pool_id"])
    return ids


def _source_block(interface_id: str, legacy: Mapping[str, Any]) -> Dict[str, Any]:
    meta = INTERFACE_META[interface_id]
    freshness_s = legacy.get("freshness_s")
    if not isinstance(freshness_s, int) or isinstance(freshness_s, bool):
        freshness_days = legacy.get("freshness_days")
        freshness_s = (
            int(freshness_days) * 86400
            if isinstance(freshness_days, int) and not isinstance(freshness_days, bool)
            else None
        )
    as_of = legacy.get("data_date") or legacy.get("date") or legacy.get("as_of")
    return ifs.source_block(
        interface_id,
        meta["source_of_truth"],
        meta["source_mode"],
        as_of=str(as_of) if as_of is not None else None,
        freshness_s=freshness_s,
        stale=bool(legacy.get("stale")),
        ids=_source_ids(interface_id, legacy),
    )


# ── envelope 构造（不改业务 payload，只叠加字段） ─────────────────────
def ok_payload(handler: Any, interface_id: str, legacy: Any) -> Dict[str, Any]:
    """成功响应：旧字段原样保留 + IFS-03 信封字段。

    兼容期碰撞处理（仅 training.episode.get 等旧契约已占用同名字段时）：
    - 旧 ``schema_version`` 存在则优先保留（信封值与之相同，如 training_episode/v1）；
    - 旧 ``source`` 若为字符串（旧契约）则保留字符串，IFS source 对象放 ``source_detail``。
    """
    if interface_id not in INTERFACE_META:
        raise ValueError("未登记的 D1 接口: %r" % (interface_id,))
    legacy_map = dict(legacy) if isinstance(legacy, Mapping) else {"data": legacy}
    data_copy = dict(legacy_map)
    request_id = request_id_for(handler)
    warnings = legacy_map.get("warnings")
    if not isinstance(warnings, list):
        warnings = []
    source_block = _source_block(interface_id, legacy_map)
    legacy_schema_version = legacy_map.get("schema_version")
    schema_version = (
        legacy_schema_version
        if isinstance(legacy_schema_version, str) and legacy_schema_version
        else INTERFACE_META[interface_id]["schema_version"]
    )
    envelope = ifs.wrap_ok(
        interface_id,
        schema_version,
        data_copy,
        source=source_block,
        warnings=warnings,
        request_id=request_id,
    )
    merged = dict(legacy_map)
    for key in ("ok", "schema_version", "request_id", "data", "warnings"):
        merged[key] = envelope[key]
    legacy_source = legacy_map.get("source")
    if isinstance(legacy_source, Mapping):
        merged["source"] = envelope["source"]
    elif legacy_source is not None:
        # 兼容期碰撞：旧 source 为字符串（例如 episodes.jsonl 相对路径）→ 保留旧值
        merged["source"] = legacy_source
        merged["source_detail"] = envelope["source"]
    else:
        merged["source"] = envelope["source"]
    merged["data"] = data_copy
    return merged


def error_payload(handler: Any, interface_id: Optional[str], legacy: Any, http_status: int = 400) -> Dict[str, Any]:
    """失败响应：旧字段保留（含旧 error 字符串）+ IFS error_detail（兼容期双形态）。"""
    legacy_map = dict(legacy) if isinstance(legacy, Mapping) else {"error": str(legacy)}
    raw_error = legacy_map.get("error")
    if isinstance(raw_error, Mapping):
        legacy_code = str(raw_error.get("code") or "internal_error")
        message = str(raw_error.get("message") or "")
    else:
        legacy_code = str(raw_error or "internal_error")
        message = str(legacy_map.get("message") or legacy_code)
    mapped_code = ifs_error_code(legacy_code, http_status)
    request_id = request_id_for(handler)
    detail = {
        "code": mapped_code,
        "message": message or mapped_code,
        "retryable": ifs.is_retryable(mapped_code),
        "details": {"legacy_code": legacy_code, "http_status": int(http_status)},
    }
    merged = dict(legacy_map)
    merged.update(
        {
            "ok": False,
            "schema_version": ERROR_SCHEMA_VERSION,
            "request_id": request_id,
            "error": legacy_code,  # 兼容期保留旧字符串（IFS 对象见 error_detail）
            "error_detail": detail,
            "warnings": [],
        }
    )
    if interface_id and interface_id in INTERFACE_META:
        source_block = _source_block(interface_id, legacy_map)
        if isinstance(legacy_map.get("source"), Mapping):
            merged["source"] = source_block
        elif legacy_map.get("source") is not None:
            merged["source"] = legacy_map["source"]  # 兼容期保留旧字符串
            merged["source_detail"] = source_block
        else:
            merged["source"] = source_block
    return merged


def send(
    handler: Any,
    obj: Any,
    code: int = 200,
    method: Optional[str] = None,
    fallback_interface: Optional[str] = None,
) -> Any:
    """统一发送入口：按 handler.path 推断接口，成功/失败都叠 envelope。

    - 未登记路径：原样 ``handler._json(obj, code)``（不接管其它接口）；
    - ``X-Request-Id`` 由 handler._ifs_request_id 交给 portal_api._json 回写。
    """
    http_method = (
        method
        or getattr(handler, "command", None)
        or getattr(handler, "_ifs_method", None)
    )
    interface_id = interface_for_path(
        getattr(handler, "path", ""), http_method, fallback_interface
    )
    if interface_id is None:
        return handler._json(obj, code)
    if isinstance(obj, Mapping) and obj.get("ok") is False:
        payload = error_payload(handler, interface_id, obj, code)
    else:
        payload = ok_payload(handler, interface_id, obj)
    handler._json(payload, code)
    return payload


def auth_error(
    handler: Any,
    method: Optional[str] = None,
    message: Optional[str] = None,
) -> Dict[str, Any]:
    """IFS G2：鉴权失败（403）统一 error/v1 信封；旧 ``error`` 字符串保留。

    ``message`` 缺省为既有 403 文案；interface_id 由路径/方法推断（未登记则不带 source）。
    """
    legacy_error = message or "forbidden: 缺失或不匹配的 X-Portal-Token"
    interface_id = interface_for_path(
        getattr(handler, "path", ""), method, None
    )
    return error_payload(handler, interface_id, {"error": legacy_error}, 403)


__all__ = [
    "ERROR_SCHEMA_VERSION",
    "INTERFACES",
    "INTERFACE_META",
    "LOCAL_INTERFACES",
    "SCHEMA_VERSION",
    "auth_error",
    "error_payload",
    "ifs_error_code",
    "interface_for_path",
    "ok_payload",
    "request_id_for",
    "send",
]
