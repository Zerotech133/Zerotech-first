# -*- coding: utf-8 -*-
"""OSLINK-W4 通道② HTTP 8795 离线协议契约（隔离树 / 合成数据）。

对应契约：`docs/plans/2026-10-25_OSLINK-W4_司马懿到田丰归口契约v0.1_草案.md` v1.0
（2026-10-26 12:20 老田代 A0 签认）；归属单 `K-QUANT-20261026-OSLEG-CH2`。

本模块只定义通道②（HTTP 8795）的请求/响应契约与内存参照对端，不做真实联网：

- 请求：build_*_request() -> dict（method / path / headers / body / query）；
- 响应：parse_*_response(status, body) -> dict（映射到 W4 生命周期状态）；
- 鉴权：X-OSLEG-Auth token（只能由调用方传入）+ X-OSLEG-Source 来源白名单；
- 幂等：X-Idempotency-Key 头，同键同体重放同一 msg_id，同键异体 409 冲突。

边界（契约 §七：代码不提供绕过口）：

- 纯离线：不建立任何网络连接、不启动 8795 端口、不 import socket/urllib/
  requests/http.client 等网络库（测试用 AST 断言 import 集合）；
- 无默认凭据：本模块内不存在任何硬编码 token，token 只允许由调用方传入；
- 不真投递：delivered 只是账本状态，不代表消息已送达、更不代表客户已读；
- 无外写：OslegHttpPeer 只在内存记账，不写盘，不产生审批/交易副作用。

通道命名空间（契约 §二 / §五-3）：

- 通道标签 = CHANNEL_HTTP（"http8795"），msg_id 前缀 "w4h-"；
- 与同进程 inbox（msg_id 前缀 "w4-"）不共享 msg_id，但共享 report_id/trace_id；
  双通道真实聚合属于"8795 接线单"范围，本模块只保证关联字段口径一致。

错误码语义（契约 §六 N5 + §七）：

- 2xx：接口已受理；201 -> submitted，其余未显式声明状态 -> accepted（接口受理，
  不得表述为已送达/已读）；
- 4xx（含 401/403/409/422）：明确拒收，一律不落账（accounted=False）；
- 5xx：status=unknown 且 retryable=True，不落账，由调用方决定是否重试；
- 缺 trace_id / report_id：契约违例，status=unknown，禁止回填默认值。

风格：中文注释；字符串之外的代码 token 只用 ASCII 标点；失败一律显式返回
{"ok": False, ...}，不静默降级。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

from quant_backtest.agent_bridge.osleg_contract import (
    CHANNEL_HTTP,
    CHANNEL_INBOX,
    STATES,
    STATE_ACCEPTED,
    STATE_ANSWERED,
    STATE_CONSUMED,
    STATE_DELIVERED,
    STATE_EXPIRED,
    STATE_REJECTED,
    STATE_SUBMITTED,
)

# ---------------------------------------------------------------- 常量与表
TOKEN_HEADER = "X-OSLEG-Auth"
SOURCE_HEADER = "X-OSLEG-Source"
IDEMPOTENCY_HEADER = "X-Idempotency-Key"
CONTENT_TYPE_HEADER = "Content-Type"
JSON_CONTENT_TYPE = "application/json"

# 来源白名单: 只有归口双方可走通道②, 未知来源 403
SOURCE_WHITELIST = ("qs1", "a0")
ALLOWED_SOURCES = SOURCE_WHITELIST

BASE_PATH = "/osleg/v1"
# 通道命名空间与 inbox 区分: 不同前缀 = 不共享 msg_id
CHANNEL_NAMESPACE = CHANNEL_HTTP
MSG_ID_PREFIX = "w4h-"
INBOX_MSG_ID_PREFIX = "w4-"

STATE_UNKNOWN = "unknown"
W4_STATES = tuple(STATES)
_W4_STATE_SET = frozenset(W4_STATES)
_ALIVE_STATES = (STATE_SUBMITTED, STATE_ACCEPTED, STATE_CONSUMED, STATE_ANSWERED)

# 非 2xx 响应体里允许显式声明的状态: 只允许"未定/拒收/过期",
# 禁止用 4xx 响应体冒充 delivered 之类已送达结论
_ALLOWED_ERROR_STATES = frozenset({STATE_UNKNOWN, STATE_REJECTED, STATE_EXPIRED})

# 占位/默认凭据一律拒绝, 防止把示例 token 当生产凭据用
_PLACEHOLDER_TOKENS = frozenset({
    "changeme", "change-me", "change_me", "default", "default-token",
    "token", "secret", "password", "test", "demo", "example", "xxx",
})

DEFAULT_HTTP_ERRORS = {
    400: "invalid_request",
    401: "token_missing",
    403: "source_forbidden",
    404: "not_found",
    409: "idempotency_conflict",
    410: "expired",
    422: "invalid_request",
}

ERROR_HTTP_STATUS = {
    "token_missing": 401,
    "token_placeholder": 401,
    "source_forbidden": 403,
    "source_mismatch": 403,
    "not_found": 404,
    "idempotency_conflict": 409,
    "idempotency_key_missing": 422,
    "state_conflict": 409,
    "invalid_endpoint": 404,
    "invalid_request": 422,
    "trace_id_missing": 422,
    "trace_mismatch": 422,
    "report_id_missing": 422,
    "report_id_mismatch": 422,
}

METHOD_GET = "GET"
METHOD_POST = "POST"


class HttpContractViolation(ValueError):
    """HTTP 契约违例（调用方必须失败关闭，不得静默降级或猜测默认值）。"""


@dataclass(frozen=True)
class EndpointSchema:
    """通道②单个端点的静态 schema（纯声明，不含任何联网动作）。"""

    name: str
    method: str
    path: str
    required_headers: tuple
    required_body: tuple
    success_http: int
    success_state: str
    idempotent: bool = True
    query_required: tuple = ()


ENDPOINTS = {
    "submit": EndpointSchema(
        name="submit",
        method=METHOD_POST,
        path=BASE_PATH + "/submit",
        required_headers=(TOKEN_HEADER, SOURCE_HEADER, IDEMPOTENCY_HEADER),
        required_body=("kind", "payload", "source", "report_id", "trace_id"),
        success_http=201,
        success_state=STATE_SUBMITTED,
    ),
    "consume": EndpointSchema(
        name="consume",
        method=METHOD_POST,
        path=BASE_PATH + "/consume",
        required_headers=(TOKEN_HEADER, SOURCE_HEADER, IDEMPOTENCY_HEADER),
        required_body=("msg_id", "consumed_by", "trace_id"),
        success_http=200,
        success_state=STATE_CONSUMED,
    ),
    "answer": EndpointSchema(
        name="answer",
        method=METHOD_POST,
        path=BASE_PATH + "/answer",
        required_headers=(TOKEN_HEADER, SOURCE_HEADER, IDEMPOTENCY_HEADER),
        required_body=("msg_id", "answered_by", "text", "report_id", "trace_id"),
        success_http=200,
        success_state=STATE_ANSWERED,
    ),
    "deliver": EndpointSchema(
        name="deliver",
        method=METHOD_POST,
        path=BASE_PATH + "/deliver",
        required_headers=(TOKEN_HEADER, SOURCE_HEADER, IDEMPOTENCY_HEADER),
        required_body=("msg_id", "channel", "trace_id"),
        success_http=200,
        success_state=STATE_DELIVERED,
    ),
    "receipt": EndpointSchema(
        name="receipt",
        method=METHOD_GET,
        path=BASE_PATH + "/receipt",
        required_headers=(TOKEN_HEADER, SOURCE_HEADER),
        required_body=(),
        success_http=200,
        success_state="ok",
        idempotent=False,
        query_required=("msg_id", "report_id"),
    ),
}

_ENDPOINT_NAMES = tuple(ENDPOINTS)


def http_status_for_error(error: str) -> int:
    """错误码 -> HTTP 状态码；未知错误码按 400 失败关闭。"""
    return ERROR_HTTP_STATUS.get(str(error or "").strip(), 400)


# ---------------------------------------------------------------- 内部工具
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical(payload) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise HttpContractViolation("payload 必须可 JSON 序列化: %s" % exc) from exc


def _digest(payload) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _normalize_headers(headers) -> dict:
    """头字段名大小写不敏感；非 dict 直接契约违例（失败关闭）。"""
    if headers is None:
        return {}
    if not isinstance(headers, Mapping):
        raise HttpContractViolation("headers 必须是 dict")
    out = {}
    for key, value in headers.items():
        name = str(key or "").strip()
        if not name:
            raise HttpContractViolation("header 名不能为空")
        out[name.lower()] = "" if value is None else str(value)
    return out


def _header_value(headers, name: str) -> str:
    return _normalize_headers(headers).get(str(name or "").strip().lower(), "")


def _as_http_status(status) -> int:
    if isinstance(status, bool) or not isinstance(status, int):
        raise HttpContractViolation("HTTP 状态码必须是 int")
    if status < 100 or status > 599:
        raise HttpContractViolation("HTTP 状态码越界: %s" % status)
    return status


def _missing(body: Mapping, field: str) -> bool:
    value = body.get(field)
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


# ---------------------------------------------------------------- 鉴权
def validate_auth(headers, allow=SOURCE_WHITELIST) -> dict:
    """校验 X-OSLEG-Auth token 与 X-OSLEG-Source 来源白名单。

    规则（契约 §二）：
    - token 缺失 -> 401 token_missing；占位/默认凭据 -> 401 token_placeholder；
    - 来源缺失或不在白名单 -> 403 source_forbidden；
    - 只有 token 非空且来源在 allow 内才 ok=True。

    token 由调用方传入，本函数不提供默认值、不回显 token 内容。
    """
    normalized = _normalize_headers(headers)
    token = normalized.get(TOKEN_HEADER.lower(), "").strip()
    source = normalized.get(SOURCE_HEADER.lower(), "").strip()
    allowed = tuple(str(item or "").strip() for item in allow)
    out = {
        "ok": False,
        "http_status": 200,
        "error": "",
        "source": source,
        "token_present": bool(token),
    }
    if not token:
        out.update(http_status=401, error="token_missing")
        return out
    if token.lower() in _PLACEHOLDER_TOKENS:
        out.update(http_status=401, error="token_placeholder")
        return out
    if not source or source not in allowed:
        out.update(http_status=403, error="source_forbidden")
        return out
    out.update(ok=True)
    return out


# ---------------------------------------------------------------- 幂等键
def idempotency_key(endpoint: str, body, *, channel: str = CHANNEL_HTTP) -> str:
    """按"通道 + 端点 + 请求体"派生确定性幂等键（同体同键，异体异键）。

    X-Idempotency-Key 头可以显式传入调用方自己的键；显式键相同但请求体不同
    时，对端必须 409 冲突拒收，而不是偷换报告或返回另一条 msg_id。
    """
    name = str(endpoint or "").strip()
    if name not in ENDPOINTS:
        raise HttpContractViolation("未登记端点: %s" % name)
    if not isinstance(body, Mapping):
        raise HttpContractViolation("body 必须是 dict")
    canonical = {
        "channel": str(channel or ""),
        "endpoint": name,
        "body": dict(body),
    }
    return "w4h-idem-" + _digest(canonical)[:32]


# ---------------------------------------------------------------- 请求构造
def _base_headers(source: str, token: str, idem_key: str = "",
                  json_body: bool = True) -> dict:
    """拼请求头；token 为空则不发鉴权头（由对端 401 拒绝，禁止本地造默认值）。"""
    headers = {}
    if json_body:
        headers[CONTENT_TYPE_HEADER] = JSON_CONTENT_TYPE
    headers[SOURCE_HEADER] = str(source or "").strip()
    token = "" if token is None else str(token).strip()
    if token:
        headers[TOKEN_HEADER] = token
    idem_key = str(idem_key or "").strip()
    if idem_key:
        headers[IDEMPOTENCY_HEADER] = idem_key
    return headers


def _request(endpoint_name: str, body: dict, headers: dict,
             query: dict | None = None) -> dict:
    schema = ENDPOINTS[endpoint_name]
    return {
        "endpoint": schema.name,
        "method": schema.method,
        "path": schema.path,
        "headers": headers,
        "body": dict(body),
        "query": dict(query or {}),
    }


def build_submit_request(kind: str, payload: dict, source: str, report_id: str,
                         trace_id: str, *, token: str,
                         idem_key: str = "") -> dict:
    """构造 submit 请求（生产方 QS1 -> 归口方 A0）。

    trace_id/report_id 缺失时不做任何回填：原样留空交给对端拒绝（status=unknown）。
    """
    if not isinstance(payload, dict):
        raise HttpContractViolation("payload 必须是对象")
    body = {
        "kind": str(kind or "").strip(),
        "payload": payload,
        "source": str(source or "").strip(),
        "report_id": str(report_id or "").strip(),
        "trace_id": str(trace_id or "").strip(),
    }
    key = str(idem_key or "").strip() or idempotency_key("submit", body)
    return _request("submit", body, _base_headers(source, token, key))


def build_consume_request(msg_id: str, consumed_by: str, trace_id: str, *,
                          token: str, source: str, idem_key: str = "") -> dict:
    """构造 consume 请求（归口方 A0 拉取消费；HTTP 通道拉取即受理）。"""
    body = {
        "msg_id": str(msg_id or "").strip(),
        "consumed_by": str(consumed_by or "").strip(),
        "trace_id": str(trace_id or "").strip(),
    }
    key = str(idem_key or "").strip() or idempotency_key("consume", body)
    return _request("consume", body, _base_headers(source, token, key))


def build_answer_request(msg_id: str, answered_by: str, text: str,
                         report_id: str, trace_id: str, *, token: str,
                         source: str, idem_key: str = "") -> dict:
    """构造 answer 请求；report_id 必须回引原提交（契约 §五-1）。"""
    body = {
        "msg_id": str(msg_id or "").strip(),
        "answered_by": str(answered_by or "").strip(),
        "text": str(text or "").strip(),
        "report_id": str(report_id or "").strip(),
        "trace_id": str(trace_id or "").strip(),
    }
    key = str(idem_key or "").strip() or idempotency_key("answer", body)
    return _request("answer", body, _base_headers(source, token, key))


def build_deliver_request(msg_id: str, channel: str, delivered_ref: str,
                          trace_id: str, *, token: str, source: str,
                          idem_key: str = "") -> dict:
    """构造 deliver 请求；channel 只是标签，对端不发送任何真实消息。"""
    body = {
        "msg_id": str(msg_id or "").strip(),
        "channel": str(channel or "").strip(),
        "delivered_ref": str(delivered_ref or "").strip(),
        "trace_id": str(trace_id or "").strip(),
    }
    key = str(idem_key or "").strip() or idempotency_key("deliver", body)
    return _request("deliver", body, _base_headers(source, token, key))


def build_receipt_request(*, token: str, source: str, msg_id: str = "",
                          report_id: str = "", trace_id: str = "") -> dict:
    """构造 receipt 查询请求；msg_id 与 report_id 至少给一个。"""
    msg_id = str(msg_id or "").strip()
    report_id = str(report_id or "").strip()
    if not msg_id and not report_id:
        raise HttpContractViolation("receipt 需要 msg_id 或 report_id")
    query = {"msg_id": msg_id, "report_id": ""} if msg_id else {
        "msg_id": "", "report_id": report_id}
    trace_id = str(trace_id or "").strip()
    if trace_id:
        query["trace_id"] = trace_id
    return _request("receipt", {}, _base_headers(source, token, json_body=False),
                    query=query)


# ---------------------------------------------------------------- 响应映射
def _failure_body(endpoint: str, error: str, status_word: str, http_status: int,
                  *, msg_id: str = "", report_id: str = "",
                  trace_id: str = "", **extra) -> dict:
    """失败关闭的响应体：ok=False + accounted=False，不落账。"""
    body = {
        "ok": False,
        "status": status_word,
        "state": status_word,
        "error": str(error or "").strip(),
        "http_status": int(http_status),
        "endpoint": str(endpoint or "").strip(),
        "accounted": False,
        "retryable": 500 <= int(http_status) < 600,
        "msg_id": str(msg_id or "").strip(),
        "report_id": str(report_id or "").strip(),
        "trace_id": str(trace_id or "").strip(),
    }
    body.update(extra)
    return body


def _record_states(body: Mapping) -> list:
    records = body.get("records")
    if not isinstance(records, (list, tuple)):
        return []
    states = []
    for rec in records:
        if not isinstance(rec, Mapping):
            continue
        state = str(rec.get("status") or rec.get("state") or "").strip()
        if state in _W4_STATE_SET:
            states.append(state)
    return states


def _records_passthrough(body: Mapping) -> list:
    records = body.get("records")
    if not isinstance(records, (list, tuple)):
        return []
    return [dict(rec) for rec in records if isinstance(rec, Mapping)]


def _str_list(value) -> list:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]


def parse_response(status, body, *, endpoint: str = "",
                   require_msg_id: bool = False,
                   require_trace_id: bool = False,
                   require_report_id: bool = False) -> dict:
    """把 HTTP 状态码 + 响应信封映射成 W4 状态语义（纯函数，不触网）。

    映射口径：
    - 2xx：accounted=True；201 无声明状态 -> submitted；其余 2xx 无声明状态 ->
      accepted（接口已受理，不是已送达）；响应声明的状态必须在 W4_STATES 内；
    - 4xx：accounted=False；响应体只允许声明 unknown/rejected/expired，否则按
      HTTP 码归类（410 -> expired，其余 -> rejected）；无 W4 记录；
    - 5xx：accounted=False、status=unknown、retryable=True；
    - 2xx 但缺必需回显字段（msg_id/trace_id/report_id）-> status=unknown，
      禁止回填默认值。

    delivered_claimed 只在响应明确声明 delivered 时为 True；接口受理/2xx 永不
    自动升级成已送达或客户已读。
    """
    http_status = _as_http_status(status)
    if body is None:
        body = {}
    if not isinstance(body, Mapping):
        raise HttpContractViolation("响应 body 必须是对象")

    msg_id = str(body.get("msg_id") or "").strip()
    report_id = str(body.get("report_id") or "").strip()
    trace_id = str(body.get("trace_id") or "").strip()
    declared = str(body.get("status") or body.get("state") or "").strip()
    error = str(body.get("error") or "").strip()
    linked_raw = body.get("linked")

    out = {
        "ok": False,
        "status": STATE_UNKNOWN,
        "state": STATE_UNKNOWN,
        "http_status": http_status,
        "accounted": False,
        "retryable": False,
        "error": error,
        "endpoint": str(endpoint or "").strip(),
        "channel": str(body.get("channel") or "").strip(),
        "msg_id": msg_id,
        "report_id": report_id,
        "trace_id": trace_id,
        "duplicate": bool(body.get("duplicate")),
        "replayed": bool(body.get("replayed")),
        "linked": None if linked_raw is None else bool(linked_raw),
        "delivered_claimed": False,
        "state_source": "",
        "conflict_with": str(body.get("conflict_with") or "").strip(),
        "record_states": [],
        "records": [],
        "channels": [],
        "msg_ids": [],
        "trace_consistent": False,
    }

    if 200 <= http_status < 300:
        if body.get("ok") is False:
            out["error"] = error or "inconsistent_envelope"
            return out
        if declared:
            if declared == "ok" and endpoint == "receipt":
                out.update(ok=True, status="ok", state="ok", accounted=True,
                           state_source="body")
                out["record_states"] = _record_states(body)
                out["records"] = _records_passthrough(body)
                out["channels"] = _str_list(body.get("channels"))
                out["msg_ids"] = _str_list(body.get("msg_ids"))
                out["trace_consistent"] = bool(body.get("trace_consistent"))
                return out
            if declared not in _W4_STATE_SET:
                out["error"] = error or "unexpected_state"
                return out
            out["state_source"] = "body"
        else:
            declared = (STATE_SUBMITTED if http_status == 201
                        else STATE_ACCEPTED)
            out["state_source"] = "http_default"
        if require_trace_id and not trace_id:
            out["error"] = error or "trace_id_missing"
            return out
        if require_msg_id and not msg_id:
            out["error"] = error or "msg_id_missing"
            return out
        if require_report_id and not report_id:
            out["error"] = error or "report_id_missing"
            return out
        out.update(ok=True, status=declared, state=declared, accounted=True,
                   delivered_claimed=(declared == STATE_DELIVERED))
        if endpoint == "receipt":
            out["record_states"] = _record_states(body)
            out["records"] = _records_passthrough(body)
            out["channels"] = _str_list(body.get("channels"))
            out["msg_ids"] = _str_list(body.get("msg_ids"))
            out["trace_consistent"] = bool(body.get("trace_consistent"))
        return out

    if 500 <= http_status < 600:
        out.update(status=STATE_UNKNOWN, state=STATE_UNKNOWN, retryable=True,
                   error=error or "server_error")
        return out

    if http_status < 400:
        # 1xx/3xx 不是合法业务响应, 失败关闭为 unknown
        out.update(status=STATE_UNKNOWN, state=STATE_UNKNOWN,
                   error=error or "unexpected_status")
        return out

    if declared in _ALLOWED_ERROR_STATES:
        out.update(status=declared, state=declared)
    elif http_status == 410:
        out.update(status=STATE_EXPIRED, state=STATE_EXPIRED)
    else:
        out.update(status=STATE_REJECTED, state=STATE_REJECTED)
    out["error"] = error or DEFAULT_HTTP_ERRORS.get(http_status, "http_error")
    return out


def _parse_endpoint_response(endpoint: str, status, body) -> dict:
    return parse_response(status, body, endpoint=endpoint,
                          require_msg_id=True, require_trace_id=True,
                          require_report_id=True)


def parse_submit_response(status, body) -> dict:
    """submit 响应映射；必须回显 msg_id/report_id/trace_id。"""
    return _parse_endpoint_response("submit", status, body)


def parse_consume_response(status, body) -> dict:
    """consume 响应映射；必须回显 msg_id/report_id/trace_id。"""
    return _parse_endpoint_response("consume", status, body)


def parse_answer_response(status, body) -> dict:
    """answer 响应映射；必须回显 msg_id/report_id/trace_id 与 linked。"""
    return _parse_endpoint_response("answer", status, body)


def parse_deliver_response(status, body) -> dict:
    """deliver 响应映射；只有显式 delivered 才算已投递（仍非客户已读）。"""
    return _parse_endpoint_response("deliver", status, body)


def parse_receipt_response(status, body) -> dict:
    """receipt 查询响应映射；只读查询，不产生 W4 状态迁移。"""
    return parse_response(status, body, endpoint="receipt")


# ---------------------------------------------------------------- 离线对端
class OslegHttpPeer:
    """通道② HTTP 8795 的离线对端参照实现（只在内存记账）。

    仅用于契约验证：不监听端口、不写盘、不建立连接、不发真实消息。
    真实 8795 网关接线必须另获独立授权（五要素），本类不提供启动入口。
    """

    def __init__(self, clock=None, allow=SOURCE_WHITELIST) -> None:
        self._clock = clock or _utcnow
        self._allow = tuple(allow)
        self._records: dict[str, dict] = {}
        self._idem: dict[tuple, dict] = {}
        self.requests_seen = 0

    # -------------------------------------------------- 只读视图
    @property
    def count(self) -> int:
        return len(self._records)

    def records(self) -> list:
        return [dict(rec) for rec in self._records.values()]

    def get(self, msg_id: str) -> dict | None:
        rec = self._records.get(str(msg_id or "").strip())
        return None if rec is None else dict(rec)

    # -------------------------------------------------- 内部
    def _now(self) -> datetime:
        dt = self._clock()
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    @staticmethod
    def _linked(rec: dict) -> bool:
        if not rec.get("answer_report_id"):
            return False
        if str(rec["answer_report_id"]) != str(rec.get("report_id") or ""):
            return False
        return str(rec.get("answer_trace_id") or "") == str(rec.get("trace_id") or "")

    def _summary(self, rec: dict) -> dict:
        state = rec["state"]
        return {
            "msg_id": rec["msg_id"],
            "channel": CHANNEL_NAMESPACE,
            "kind": rec["kind"],
            "state": state,
            "status": state,
            "report_id": rec["report_id"],
            "trace_id": rec["trace_id"],
            "alive": state in _ALIVE_STATES,
            "duplicate": int(rec.get("duplicates") or 0) > 0,
            "linked": self._linked(rec),
            "retry_of": rec.get("retry_of") or "",
            "submitted_at": rec.get("submitted_at") or "",
            "accepted_by": rec.get("accepted_by") or "",
            "answered_by": rec.get("answered_by") or "",
            "delivered_channel": rec.get("delivered_channel") or "",
        }

    def _envelope(self, rec: dict, **over) -> dict:
        body = {
            "ok": True,
            "status": rec["state"],
            "state": rec["state"],
            "msg_id": rec["msg_id"],
            "report_id": rec["report_id"],
            "trace_id": rec["trace_id"],
            "channel": CHANNEL_NAMESPACE,
            "accounted": True,
            "duplicate": int(rec.get("duplicates") or 0) > 0,
            "replayed": False,
        }
        body.update(over)
        return body

    # -------------------------------------------------- 入口
    def handle(self, request) -> tuple:
        """处理一条请求 dict，返回 (http_status, body)；不触网、不落盘。"""
        if not isinstance(request, Mapping):
            raise HttpContractViolation("请求必须是 dict")
        self.requests_seen += 1
        endpoint = str(request.get("endpoint") or "").strip()
        schema = ENDPOINTS.get(endpoint)
        if schema is None:
            return 404, _failure_body(endpoint, "invalid_endpoint",
                                      STATE_REJECTED, 404)
        headers = request.get("headers") or {}
        auth = validate_auth(headers, self._allow)
        if not auth["ok"]:
            return auth["http_status"], _failure_body(
                endpoint, auth["error"], STATE_REJECTED, auth["http_status"])

        body = request.get("body")
        if body is None:
            body = {}
        if not isinstance(body, Mapping):
            raise HttpContractViolation("请求 body 必须是对象")
        body = dict(body)
        query = request.get("query")
        if query is None:
            query = {}
        if not isinstance(query, Mapping):
            raise HttpContractViolation("query 必须是对象")
        query = dict(query)

        idem = ""
        if schema.idempotent:
            idem = _header_value(headers, IDEMPOTENCY_HEADER).strip()
            if not idem:
                return 422, _failure_body(endpoint, "idempotency_key_missing",
                                          STATE_REJECTED, 422)
            digest = _digest(body)
            seen = self._idem.get((endpoint, idem))
            if seen is not None:
                if seen["digest"] != digest:
                    conflict = _failure_body(
                        endpoint, "idempotency_conflict", STATE_REJECTED, 409)
                    conflict["conflict_with"] = seen.get("msg_id") or ""
                    return 409, conflict
                replay = dict(seen["body"])
                replay.update(replayed=True, duplicate=True)
                if endpoint == "submit":
                    rec = self._records.get(seen.get("msg_id") or "")
                    if rec is not None:
                        rec["duplicates"] = int(rec.get("duplicates") or 0) + 1
                        replay["status"] = rec["state"]
                        replay["state"] = rec["state"]
                return 200, replay

        handler = {
            "submit": self._handle_submit,
            "consume": self._handle_consume,
            "answer": self._handle_answer,
            "deliver": self._handle_deliver,
            "receipt": self._handle_receipt,
        }[endpoint]
        code, resp = handler(body, query, auth, headers)
        if 200 <= code < 300 and schema.idempotent:
            self._idem[(endpoint, idem)] = {
                "digest": _digest(body),
                "msg_id": str(resp.get("msg_id") or ""),
                "code": code,
                "body": dict(resp),
            }
        return code, resp

    # -------------------------------------------------- 各端点
    def _handle_submit(self, body: Mapping, query: Mapping, auth: Mapping,
                       headers: Mapping) -> tuple:
        trace_id = str(body.get("trace_id") or "").strip()
        report_id = str(body.get("report_id") or "").strip()
        if not trace_id:
            return 422, _failure_body("submit", "trace_id_missing",
                                      STATE_UNKNOWN, 422, report_id=report_id)
        if not report_id:
            return 422, _failure_body("submit", "report_id_missing",
                                      STATE_UNKNOWN, 422, trace_id=trace_id)
        kind = str(body.get("kind") or "").strip()
        source = str(body.get("source") or "").strip()
        payload = body.get("payload")
        if not kind or not source or not isinstance(payload, Mapping):
            return 422, _failure_body("submit", "invalid_request",
                                      STATE_REJECTED, 422, report_id=report_id,
                                      trace_id=trace_id)
        if source not in self._allow:
            return 403, _failure_body("submit", "source_forbidden",
                                      STATE_REJECTED, 403,
                                      report_id=report_id, trace_id=trace_id)
        auth_source = str(auth.get("source") or "").strip()
        if auth_source and source != auth_source:
            return 403, _failure_body("submit", "source_mismatch",
                                      STATE_REJECTED, 403,
                                      report_id=report_id, trace_id=trace_id)
        now = self._now()
        msg_id = MSG_ID_PREFIX + uuid.uuid4().hex[:12]
        rec = {
            "msg_id": msg_id,
            "channel": CHANNEL_NAMESPACE,
            "kind": kind,
            "payload": dict(payload),
            "source": source,
            "report_id": report_id,
            "trace_id": trace_id,
            "state": STATE_SUBMITTED,
            "submitted_at": _iso(now),
            "submitted_epoch": now.timestamp(),
            "duplicates": 0,
            "retry_of": "",
            "accepted_by": "",
            "consumed_by": "",
            "answered_by": "",
            "answer_text": "",
            "answer_report_id": "",
            "answer_trace_id": "",
            "delivered_channel": "",
            "delivered_ref": "",
        }
        self._records[msg_id] = rec
        return 201, self._envelope(rec)

    def _handle_consume(self, body: Mapping, query: Mapping, auth: Mapping,
                        headers: Mapping) -> tuple:
        trace_id = str(body.get("trace_id") or "").strip()
        msg_id = str(body.get("msg_id") or "").strip()
        if not trace_id:
            return 422, _failure_body("consume", "trace_id_missing",
                                      STATE_UNKNOWN, 422, msg_id=msg_id)
        rec = self._records.get(msg_id)
        if rec is None:
            return 404, _failure_body("consume", "not_found",
                                      STATE_REJECTED, 404, msg_id=msg_id)
        if trace_id != rec["trace_id"]:
            return 422, _failure_body("consume", "trace_mismatch",
                                      STATE_UNKNOWN, 422, msg_id=msg_id,
                                      report_id=rec["report_id"])
        consumed_by = str(body.get("consumed_by") or "").strip()
        if not consumed_by:
            return 422, _failure_body("consume", "invalid_request",
                                      STATE_REJECTED, 422, msg_id=msg_id,
                                      report_id=rec["report_id"],
                                      trace_id=rec["trace_id"])
        if rec["state"] not in (STATE_SUBMITTED, STATE_ACCEPTED):
            return 409, _failure_body("consume", "state_conflict",
                                      STATE_REJECTED, 409, msg_id=msg_id,
                                      report_id=rec["report_id"],
                                      trace_id=rec["trace_id"])
        now = _iso(self._now())
        # HTTP 通道把归口受理与消费合并: A0 拉取即视为受理 (不另开 accept 端点)
        rec["accepted_by"] = consumed_by
        rec["accepted_at"] = now
        rec["consumed_by"] = consumed_by
        rec["consumed_at"] = now
        rec["state"] = STATE_CONSUMED
        return 200, self._envelope(rec)

    def _handle_answer(self, body: Mapping, query: Mapping, auth: Mapping,
                       headers: Mapping) -> tuple:
        trace_id = str(body.get("trace_id") or "").strip()
        msg_id = str(body.get("msg_id") or "").strip()
        if not trace_id:
            return 422, _failure_body("answer", "trace_id_missing",
                                      STATE_UNKNOWN, 422, msg_id=msg_id)
        rec = self._records.get(msg_id)
        if rec is None:
            return 404, _failure_body("answer", "not_found",
                                      STATE_REJECTED, 404, msg_id=msg_id)
        if trace_id != rec["trace_id"]:
            return 422, _failure_body("answer", "trace_mismatch",
                                      STATE_UNKNOWN, 422, msg_id=msg_id,
                                      report_id=rec["report_id"])
        if rec["state"] != STATE_CONSUMED:
            return 409, _failure_body("answer", "state_conflict",
                                      STATE_REJECTED, 409, msg_id=msg_id,
                                      report_id=rec["report_id"],
                                      trace_id=rec["trace_id"])
        report_id = str(body.get("report_id") or "").strip()
        if not report_id:
            # 契约 §六 N4: 答复缺 report_id -> 拒收, linked=False, 不写 answered
            return 422, _failure_body("answer", "report_id_missing",
                                      STATE_REJECTED, 422, msg_id=msg_id,
                                      trace_id=rec["trace_id"], linked=False)
        if report_id != rec["report_id"]:
            return 422, _failure_body("answer", "report_id_mismatch",
                                      STATE_REJECTED, 422, msg_id=msg_id,
                                      trace_id=rec["trace_id"], linked=False)
        text = str(body.get("text") or "").strip()
        answered_by = str(body.get("answered_by") or "").strip()
        if not text or not answered_by:
            return 422, _failure_body("answer", "invalid_request",
                                      STATE_REJECTED, 422, msg_id=msg_id,
                                      report_id=rec["report_id"],
                                      trace_id=rec["trace_id"])
        rec["answer_text"] = text
        rec["answered_by"] = answered_by
        rec["answered_at"] = _iso(self._now())
        rec["answer_report_id"] = rec["report_id"]
        rec["answer_trace_id"] = rec["trace_id"]
        rec["state"] = STATE_ANSWERED
        return 200, self._envelope(rec, linked=True)

    def _handle_deliver(self, body: Mapping, query: Mapping, auth: Mapping,
                        headers: Mapping) -> tuple:
        trace_id = str(body.get("trace_id") or "").strip()
        msg_id = str(body.get("msg_id") or "").strip()
        if not trace_id:
            return 422, _failure_body("deliver", "trace_id_missing",
                                      STATE_UNKNOWN, 422, msg_id=msg_id)
        rec = self._records.get(msg_id)
        if rec is None:
            return 404, _failure_body("deliver", "not_found",
                                      STATE_REJECTED, 404, msg_id=msg_id)
        if trace_id != rec["trace_id"]:
            return 422, _failure_body("deliver", "trace_mismatch",
                                      STATE_UNKNOWN, 422, msg_id=msg_id,
                                      report_id=rec["report_id"])
        if rec["state"] != STATE_ANSWERED:
            return 409, _failure_body("deliver", "state_conflict",
                                      STATE_REJECTED, 409, msg_id=msg_id,
                                      report_id=rec["report_id"],
                                      trace_id=rec["trace_id"])
        channel = str(body.get("channel") or "").strip()
        if not channel:
            return 422, _failure_body("deliver", "invalid_request",
                                      STATE_REJECTED, 422, msg_id=msg_id,
                                      report_id=rec["report_id"],
                                      trace_id=rec["trace_id"])
        rec["delivered_channel"] = channel
        rec["delivered_ref"] = str(body.get("delivered_ref") or "").strip()
        rec["delivered_at"] = _iso(self._now())
        rec["state"] = STATE_DELIVERED
        return 200, self._envelope(rec, delivered_channel=channel)

    def _handle_receipt(self, body: Mapping, query: Mapping, auth: Mapping,
                        headers: Mapping) -> tuple:
        msg_id = str(query.get("msg_id") or "").strip()
        report_id = str(query.get("report_id") or "").strip()
        trace_id = str(query.get("trace_id") or "").strip()
        if not msg_id and not report_id:
            return 422, _failure_body("receipt", "invalid_request",
                                      STATE_REJECTED, 422)
        if msg_id:
            rec = self._records.get(msg_id)
            if rec is None:
                return 404, _failure_body("receipt", "not_found",
                                          STATE_REJECTED, 404, msg_id=msg_id)
            if trace_id and trace_id != rec["trace_id"]:
                return 422, _failure_body("receipt", "trace_mismatch",
                                          STATE_UNKNOWN, 422, msg_id=msg_id,
                                          report_id=rec["report_id"])
            summary = self._summary(rec)
            summary.update(ok=True, status=rec["state"], state=rec["state"],
                           accounted=True, records=[self._summary(rec)],
                           channels=[CHANNEL_NAMESPACE],
                           msg_ids=[rec["msg_id"]], total=1,
                           trace_ids=[rec["trace_id"]],
                           trace_consistent=True)
            return 200, summary
        rows = [rec for rec in self._records.values()
                if rec["report_id"] == report_id]
        if not rows:
            return 404, _failure_body("receipt", "not_found", STATE_REJECTED,
                                      404, report_id=report_id)
        traces = sorted({rec["trace_id"] for rec in rows})
        answered = [rec for rec in rows
                    if rec["state"] in (STATE_ANSWERED, STATE_DELIVERED)]
        return 200, {
            "ok": True,
            "status": "ok",
            "state": "ok",
            "accounted": True,
            "report_id": report_id,
            "msg_ids": [rec["msg_id"] for rec in rows],
            "channels": [CHANNEL_NAMESPACE],
            "records": [self._summary(rec) for rec in rows],
            "total": len(rows),
            "trace_ids": traces,
            "trace_consistent": len(traces) == 1,
            "linked": bool(answered) and all(self._linked(rec)
                                             for rec in answered),
            "alive": any(rec["state"] in _ALIVE_STATES for rec in rows),
            "duplicate": any(int(rec.get("duplicates") or 0) > 0
                             for rec in rows),
        }


__all__ = [
    "ALLOWED_SOURCES",
    "BASE_PATH",
    "CHANNEL_INBOX",
    "CHANNEL_NAMESPACE",
    "CONTENT_TYPE_HEADER",
    "DEFAULT_HTTP_ERRORS",
    "ENDPOINTS",
    "ERROR_HTTP_STATUS",
    "EndpointSchema",
    "HttpContractViolation",
    "IDEMPOTENCY_HEADER",
    "INBOX_MSG_ID_PREFIX",
    "JSON_CONTENT_TYPE",
    "METHOD_GET",
    "METHOD_POST",
    "MSG_ID_PREFIX",
    "OslegHttpPeer",
    "SOURCE_HEADER",
    "SOURCE_WHITELIST",
    "STATE_UNKNOWN",
    "TOKEN_HEADER",
    "W4_STATES",
    "build_answer_request",
    "build_consume_request",
    "build_deliver_request",
    "build_receipt_request",
    "build_submit_request",
    "http_status_for_error",
    "idempotency_key",
    "parse_answer_response",
    "parse_consume_response",
    "parse_deliver_response",
    "parse_receipt_response",
    "parse_response",
    "parse_submit_response",
    "validate_auth",
]
