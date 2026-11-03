# -*- coding: utf-8 -*-
r"""IFS v1 统一 envelope / error 适配器（F1，WO-IFS-SCHEMA-20261103）。

只依赖 Python 标准库；**不联网、不读写文件、不连 PG、不启动服务**，只做纯数据
包装/校验，供现有 API/脚本按需接入（不强制改造现有 API）。

契约权威（本模块与其逐项对齐）：

- `docs/standards/2026-11-03_Envelope_Error_v1.md`
- `docs/standards/schemas/interface_envelope_v1.schema.json`
- `docs/standards/schemas/interface_error_v1.schema.json`
- `docs/standards/INTERFACE_REGISTRY_v1.yaml`（`envelope` 段：21 个错误码）
- `docs/standards/2026-11-03_接口对接规范_IFS_v1.md`（IFS-03/IFS-06/IFS-10）

用法::

    from agent_platform.contracts import ifs

    payload = ifs.wrap_ok(
        "d1.research.get", "d1_research/v1", {"items": []},
        source_of_truth="agent_platform/data/pool_review/",
        source_mode="aggregate", as_of="2026-11-03",
        request_id="req-0123456789abcdef0123456789abcdef",
    )
    assert ifs.validate_envelope(payload) == []

    err = ifs.wrap_error("bad_body", "非法 JSON 请求体")
    assert err["schema_version"] == "error/v1"
    assert ifs.error_http_status("bad_body") == 400
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any, Dict, Iterable, List, Mapping, Optional

# ── 契约常量（与 registry `envelope` 段对齐） ────────────────────────
ERROR_SCHEMA_VERSION = "error/v1"
REQUEST_ID_PATTERN = r"^req-[0-9a-f]{32}$"
SCHEMA_VERSION_PATTERN = r"^[a-z0-9_.-]+/v[0-9]+$"
INTERFACE_ID_PATTERN = r"^[a-z0-9][a-z0-9_.-]*$"

SUCCESS_FIELDS = ("ok", "schema_version", "request_id", "source", "data", "warnings")
SUCCESS_OPTIONAL_FIELDS = ("generated_at", "trace_id")
ERROR_FIELDS = ("ok", "schema_version", "request_id", "error")
ERROR_DETAIL_FIELDS = ("code", "message", "retryable", "details")
SOURCE_MODES = ("pg_official", "projection", "ledger", "static", "aggregate", "manifest")
SOURCE_REQUIRED_FIELDS = ("interface_id", "source_of_truth", "source_mode")

_SAFE_ID_KEYS = (
    "pool_id",
    "card_id",
    "case_id",
    "decision_id",
    "handoff_id",
    "watch_plan_id",
    "trigger_id",
    "confirmation_id",
    "trade_id",
    "outcome_id",
    "training_episode_id",
)
_RESERVED_SOURCE_KEYS = set(SOURCE_REQUIRED_FIELDS) | {
    "as_of",
    "freshness_s",
    "stale",
}

#: 21 个错误码（http / retryable / scene），与 registry `envelope.error_codes` 一致
ERROR_CODES: Dict[str, Dict[str, Any]] = {
    "invalid_request": {"http": 400, "retryable": False, "scene": "请求格式/方法/路由不匹配"},
    "invalid_parameter": {"http": 400, "retryable": False, "scene": "参数取值非法"},
    "bad_body": {"http": 400, "retryable": False, "scene": "body 非 JSON / 非 object"},
    "execution_field_forbidden": {
        "http": 400,
        "retryable": False,
        "scene": "handoff/确认携带 order/qty/broker 等执行字段（IFS-05）",
    },
    "bad_target_action": {
        "http": 400,
        "retryable": False,
        "scene": "target/action 组合非法（watch/trade 目标域）",
    },
    "unauthorized": {"http": 401, "retryable": False, "scene": "缺 portal-token；8787 401 为登录门"},
    "forbidden": {"http": 403, "retryable": False, "scene": "roles 不足"},
    "not_paper": {"http": 403, "retryable": False, "scene": "paper_only=true 被违反"},
    "not_found": {"http": 404, "retryable": False, "scene": "资源不存在"},
    "route_not_found": {"http": 404, "retryable": False, "scene": "未登记路由"},
    "conflict": {"http": 409, "retryable": False, "scene": "状态冲突（已确认/已执行）"},
    "duplicate": {"http": 409, "retryable": False, "scene": "重复创建但语义非幂等"},
    "idempotent_replay": {
        "http": 200,
        "retryable": False,
        "scene": "同 idempotency_key/source 返回原记录（ok=true + warning）",
    },
    "unprocessable": {"http": 422, "retryable": False, "scene": "业务规则拒绝（风控/证据不足）"},
    "rate_limited": {"http": 429, "retryable": True, "scene": "限流，按 Retry-After 退避"},
    "internal_error": {"http": 500, "retryable": True, "scene": "接口内部异常（掩码堆栈）"},
    "upstream_unavailable": {"http": 502, "retryable": True, "scene": "上游依赖不可用"},
    "not_ready": {"http": 503, "retryable": True, "scene": "依赖未就绪（如上下文预热）"},
    "data_unavailable": {"http": 503, "retryable": True, "scene": "数据缺失/未生成（不造数）"},
    "kill_switch_active": {"http": 503, "retryable": False, "scene": "PAPER_KILL 生效"},
    "timeout": {"http": 504, "retryable": True, "scene": "处理超时"},
}

_REQUEST_ID_RE = re.compile(REQUEST_ID_PATTERN)
_SCHEMA_VERSION_RE = re.compile(SCHEMA_VERSION_PATTERN)
_INTERFACE_ID_RE = re.compile(INTERFACE_ID_PATTERN)


# ── 基础工具 ──────────────────────────────────────────────────────────
def new_request_id() -> str:
    """生成 IFS 规范的 request_id（`req-` + 32 hex 小写）。"""
    return "req-" + uuid.uuid4().hex


def now_iso() -> str:
    """本地 ISO 风格时间戳（与 F0 信封/其它只读接口同口径）。"""
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def error_http_status(code: str) -> int:
    """错误码 → HTTP 状态码；未登记错误码抛 ValueError（closed-on-failure）。"""
    spec = ERROR_CODES.get(code)
    if spec is None:
        raise ValueError(
            "未登记错误码: %r（允许: %s）" % (code, ", ".join(sorted(ERROR_CODES)))
        )
    return int(spec["http"])


def is_retryable(code: str) -> bool:
    """错误码默认是否可重试（可在 wrap_error 中显式覆盖）。"""
    return bool(ERROR_CODES[code]["retryable"])


def source_block(
    interface_id: str,
    source_of_truth: str,
    source_mode: str = "ledger",
    *,
    as_of: Optional[str] = None,
    freshness_s: Optional[int] = None,
    stale: bool = False,
    ids: Optional[Mapping[str, Any]] = None,
) -> dict:
    """构造 IFS-03 `source` 对象（真源/来源模式/时点/新鲜度 + 可选 ID chain 字段）。"""
    if not isinstance(interface_id, str) or not _INTERFACE_ID_RE.match(interface_id):
        raise ValueError("interface_id 非法: %r" % (interface_id,))
    if not isinstance(source_of_truth, str) or not source_of_truth.strip():
        raise ValueError("source_of_truth 必须为非空字符串")
    if source_mode not in SOURCE_MODES:
        raise ValueError(
            "source_mode 非法: %r（允许: %s）" % (source_mode, ", ".join(SOURCE_MODES))
        )
    if freshness_s is not None and (
        isinstance(freshness_s, bool) or not isinstance(freshness_s, int) or freshness_s < 0
    ):
        raise ValueError("freshness_s 必须为非负整数或 None: %r" % (freshness_s,))
    block: Dict[str, Any] = {
        "interface_id": interface_id,
        "source_of_truth": source_of_truth.strip(),
        "source_mode": source_mode,
        "stale": bool(stale),
    }
    if as_of is not None:
        block["as_of"] = str(as_of)
    if freshness_s is not None:
        block["freshness_s"] = int(freshness_s)
    for key, value in (ids or {}).items():
        if key in _RESERVED_SOURCE_KEYS:
            raise ValueError("source ids 不得覆盖保留字段: %s" % key)
        block[str(key)] = value
    return block


# ── 成功 / 失败信封 ──────────────────────────────────────────────────
def wrap_ok(
    interface_id: str,
    schema_version: str,
    data: Any,
    *,
    source: Optional[Mapping[str, Any]] = None,
    source_of_truth: Optional[str] = None,
    source_mode: Optional[str] = None,
    as_of: Optional[str] = None,
    freshness_s: Optional[int] = None,
    stale: bool = False,
    ids: Optional[Mapping[str, Any]] = None,
    warnings: Optional[Iterable[Mapping[str, Any]]] = None,
    request_id: Optional[str] = None,
    generated_at: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> dict:
    """构造 IFS-03 成功信封（ok/schema_version/request_id/source/data/warnings）。

    `source` 可显式给出；否则用 `source_of_truth` + `source_mode` 构造。
    `warnings` 缺省为 `[]`；`generated_at`/`trace_id` 可选（建议 generated_at）。
    """
    if not isinstance(schema_version, str) or not _SCHEMA_VERSION_RE.match(schema_version):
        raise ValueError("schema_version 非法: %r" % (schema_version,))
    if source is None:
        if not source_of_truth or not source_mode:
            raise ValueError("source 缺省时必须提供 source_of_truth + source_mode")
        source_block_value = source_block(
            interface_id,
            source_of_truth,
            source_mode,
            as_of=as_of,
            freshness_s=freshness_s,
            stale=stale,
            ids=ids,
        )
    else:
        if not isinstance(source, Mapping):
            raise ValueError("source 必须为 dict")
        missing = [
            field for field in SOURCE_REQUIRED_FIELDS if not source.get(field)
        ]
        if missing:
            raise ValueError("source 缺字段: %s" % ", ".join(missing))
        if source.get("source_mode") not in SOURCE_MODES:
            raise ValueError("source.source_mode 非法: %r" % (source.get("source_mode"),))
        source_block_value = dict(source)

    request_id_value = request_id or new_request_id()
    if not _REQUEST_ID_RE.match(str(request_id_value)):
        raise ValueError("request_id 非法: %r（应为 req-<32 hex>）" % (request_id_value,))
    warn_list: List[Dict[str, Any]] = []
    for item in warnings or []:
        if not isinstance(item, Mapping):
            raise ValueError("warnings 元素必须为 dict: %r" % (item,))
        if not item.get("code") or not item.get("message"):
            raise ValueError("warnings 元素必须含 code/message: %r" % (item,))
        warn_list.append(dict(item))

    envelope: Dict[str, Any] = {
        "ok": True,
        "schema_version": schema_version,
        "request_id": str(request_id_value),
        "source": source_block_value,
        "data": data,
        "warnings": warn_list,
    }
    if generated_at is not None:
        envelope["generated_at"] = str(generated_at)
    if trace_id is not None:
        envelope["trace_id"] = str(trace_id)
    return envelope


def wrap_error(
    code: str,
    message: str,
    *,
    details: Optional[Mapping[str, Any]] = None,
    request_id: Optional[str] = None,
    retryable: Optional[bool] = None,
    http_status: Optional[int] = None,
    param: Optional[str] = None,
    retry_after_s: Optional[int] = None,
) -> dict:
    """构造 IFS-03 错误信封（ok=false + schema_version=error/v1 + error{...}）。

    `code` 必须在 ERROR_CODES 内；`retryable` 缺省取错误码默认（可覆盖）；
    `http_status` 仅作调用方参考校验（HTTP 层由调用方发送）。
    """
    if not isinstance(code, str) or code not in ERROR_CODES:
        raise ValueError(
            "未登记错误码: %r（允许: %s）" % (code, ", ".join(sorted(ERROR_CODES)))
        )
    if not isinstance(message, str) or not message.strip():
        raise ValueError("message 必须为非空字符串")
    default_http = int(ERROR_CODES[code]["http"])
    if http_status is not None and http_status != default_http:
        raise ValueError(
            "http_status 与错误码登记不一致: %r vs %d" % (http_status, default_http)
        )
    request_id_value = request_id or new_request_id()
    if not _REQUEST_ID_RE.match(str(request_id_value)):
        raise ValueError("request_id 非法: %r（应为 req-<32 hex>）" % (request_id_value,))
    detail: Dict[str, Any] = {
        "code": code,
        "message": message.strip(),
        "retryable": is_retryable(code) if retryable is None else bool(retryable),
        "details": dict(details or {}),
    }
    if param is not None:
        detail["param"] = str(param)
    if retry_after_s is not None:
        detail["retry_after_s"] = int(retry_after_s)
    return {
        "ok": False,
        "schema_version": ERROR_SCHEMA_VERSION,
        "request_id": str(request_id_value),
        "error": detail,
    }


# ── 信封校验（契约测试/接口自检可复用） ──────────────────────────────
def validate_envelope(payload: Any) -> List[str]:
    """校验成功信封，返回错误列表（空列表 = 通过）。"""
    errors: List[str] = []
    if not isinstance(payload, Mapping):
        return ["payload 必须为 object"]
    for field in SUCCESS_FIELDS:
        if field not in payload:
            errors.append("missing:%s" % field)
    if payload.get("ok") is not True:
        errors.append("ok 必须为 true")
    schema_version = payload.get("schema_version")
    if not isinstance(schema_version, str) or not _SCHEMA_VERSION_RE.match(schema_version):
        errors.append("schema_version 非法: %r" % (schema_version,))
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not _REQUEST_ID_RE.match(request_id):
        errors.append("request_id 非法: %r" % (request_id,))
    source = payload.get("source")
    if not isinstance(source, Mapping):
        errors.append("source 必须为 object")
    else:
        for field in SOURCE_REQUIRED_FIELDS:
            if not isinstance(source.get(field), str) or not source.get(field):
                errors.append("source.%s 缺失/非法" % field)
        if source.get("source_mode") not in SOURCE_MODES:
            errors.append("source.source_mode 非法: %r" % (source.get("source_mode"),))
    warnings = payload.get("warnings")
    if not isinstance(warnings, list):
        errors.append("warnings 必须为 list（无告警用 []）")
    else:
        for item in warnings:
            if not isinstance(item, Mapping) or not item.get("code") or not item.get("message"):
                errors.append("warnings 元素必须含 code/message: %r" % (item,))
    return errors


def validate_error(payload: Any) -> List[str]:
    """校验错误信封，返回错误列表（空列表 = 通过）。"""
    errors: List[str] = []
    if not isinstance(payload, Mapping):
        return ["payload 必须为 object"]
    for field in ERROR_FIELDS:
        if field not in payload:
            errors.append("missing:%s" % field)
    if payload.get("ok") is not False:
        errors.append("ok 必须为 false")
    if payload.get("schema_version") != ERROR_SCHEMA_VERSION:
        errors.append("schema_version 必须为 %s" % ERROR_SCHEMA_VERSION)
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not _REQUEST_ID_RE.match(request_id):
        errors.append("request_id 非法: %r" % (request_id,))
    detail = payload.get("error")
    if not isinstance(detail, Mapping):
        errors.append("error 必须为 object")
    else:
        for field in ERROR_DETAIL_FIELDS:
            if field not in detail:
                errors.append("error 缺 %s" % field)
        if detail.get("code") not in ERROR_CODES:
            errors.append("error.code 未登记: %r" % (detail.get("code"),))
        if not isinstance(detail.get("retryable"), bool):
            errors.append("error.retryable 必须为 bool")
        if not isinstance(detail.get("details"), Mapping):
            errors.append("error.details 必须为 object")
    return errors


__all__ = [
    "ERROR_CODES",
    "ERROR_DETAIL_FIELDS",
    "ERROR_FIELDS",
    "ERROR_SCHEMA_VERSION",
    "INTERFACE_ID_PATTERN",
    "REQUEST_ID_PATTERN",
    "SCHEMA_VERSION_PATTERN",
    "SOURCE_MODES",
    "SUCCESS_FIELDS",
    "error_http_status",
    "is_retryable",
    "new_request_id",
    "now_iso",
    "source_block",
    "validate_envelope",
    "validate_error",
    "wrap_error",
    "wrap_ok",
]
