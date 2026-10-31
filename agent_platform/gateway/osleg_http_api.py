# -*- coding: utf-8 -*-
"""OSLINK-W4 通道② 8795 HTTP 端点层 + 鉴权（离线 / 隔离 store / 不启端口）。

对应契约：`docs/plans/2026-10-25_OSLINK-W4_司马懿到田丰归口契约v0.1_草案.md` v1.0
（2026-10-26 12:20 老田代 A0 归口签认）；离线协议口径：
`quant_backtest/agent_bridge/osleg_http_contract.py`（CH2 参照对端）；归属单
`K-QUANT-20261026-OSLEG-8795API`（W4 HTTP 端点层，离线 P0）。

职责边界（不扩范围、不造第二套语义）：

- 只做「HTTP 请求 -> `gateway.inbox_gateway` 调用 -> W4 响应信封」的翻译层；
  gateway 的幂等与证据门槛原样复用（不放松、不绕过、不改 `inbox_gateway.py`）。
- gateway 没有的 W4 字段（答复正文、`report_id`/`trace_id` 回引、W4 生命周期状态）
  落**同一 store 根**下的 sidecar：`<store>/osleg_http/`，全部 tmp + `os.replace`
  原子写；sidecar 只做只读写回与状态映射，不修改 gateway 账结构。
- 五端点（路径/方法与 CH2 `ENDPOINTS` 一致）：

  ==========  ======  ======================  ========================
  端点        方法    路径                    翻译到 gateway
  ==========  ======  ======================  ========================
  submit      POST    /osleg/v1/submit        ``submit(kind, payload)``
  consume     POST    /osleg/v1/consume       ``consume(msg_id, answered_by, answer_ref)``
  answer      POST    /osleg/v1/answer        仅写 sidecar（gateway 无答复正文语义）
  deliver     POST    /osleg/v1/deliver       ``deliver(msg_id, channel, delivered_ref)``
  receipt     GET     /osleg/v1/receipt       ``receipt(msg_id)`` + sidecar 对账
  ==========  ======  ======================  ========================

- 鉴权：`X-OSLEG-Auth` token 只能由**环境**注入——env `OSLEG_HTTP_TOKEN` 或文件
  `agent_platform/gateway/.osleg_http_token`（路径可用 env `OSLEG_HTTP_TOKEN_FILE`
  覆盖，便于隔离测试）；常量时间比较（`hmac.compare_digest`）。本模块**不含任何
  默认/硬编码 token**：未配置或配成占位符 -> 503 失败关闭；缺失/错误 -> 401。
  `X-OSLEG-Source` 必须在 `{qs1, a0}` 内，否则 403。
- 幂等：POST 必须带 `X-Idempotency-Key`；同键同体重放同一 `msg_id`（200、
  replayed=True），同键异体 409 冲突（`conflict_with` 指向原单）。幂等登记只落
  2xx 结果，4xx/5xx 一律不落账。
- store：必须显式注入（`set_store(root)`，或 env `OSLEG_HTTP_STORE_ROOT` 指向
  隔离根）；未配置 -> 503 失败关闭，**绝不隐式写真实
  `agent_platform/bridge_inbox`**（离线单不启 8795、不接真实消费者）。
- 离线：本模块不监听端口、不联网，不 import socket/urllib/requests/http.client；
  仅被 `gateway/serve.py` 在 env `OSLEG_HTTP_API` 开启时路由（默认关闭）。

已知边界与未决点（接线单前必须处理，本单不擅自扩范围）：

- CH2 `build_consume_request()` 的请求体只有 `msg_id/consumed_by/trace_id`，不含
  gateway 证据字段 `answered_by/answer_ref`；本层按"证据门槛不放松"要求两者必填
  （缺即 422），因此当前 CH2 请求构造器不能直接打到本层 —— 需在 8795 接线单
  补契约字段（或由归口方显式给出证据），属契约 v1.1 待补项。
- gateway 写入成功后 sidecar 落盘失败会返回 5xx（半写）：本层不伪装成功，重试时
  gateway 幂等键可回放同一条入箱记录；sidecar 缺失走失败关闭（不猜 report_id/
  trace_id）。

W4 生命周期映射（契约 §四；HTTP 通道照 CH2 参照对端口径把 accepted 并入
consume —— 归口方拉取即受理，不另开 accept 端点）::

    submitted -> consumed -> answered -> delivered
    receipt: gateway inbox              -> submitted（alive=True）
             gateway processed + 已消费  -> consumed（alive=True）
             sidecar 已写答复            -> answered（alive=True, answered=True）
             gateway/sidecar 已投递      -> delivered（alive=False, delivered=True）

响应口径与 `osleg_http_contract.parse_*_response` 兼容：2xx -> ok=True/
accounted=True；4xx -> rejected（缺 trace/回执差异类 -> unknown）且
accounted=False；5xx -> unknown + retryable=True；缺 `trace_id`/`report_id`
不填默认值、直接拒收。

用法（仅隔离/离线验证，生产接线另获独立授权）::

    from gateway import osleg_http_api
    osleg_http_api.set_store("<隔离根目录>")   # 显式注入 store，测试用 tmp
    status, body = osleg_http_api.handle(
        method="POST", path="/osleg/v1/submit",
        headers={"X-OSLEG-Auth": token, "X-OSLEG-Source": "qs1",
                 "X-Idempotency-Key": "k1"},
        body={"kind": "stock_consult_result", "payload": {"q": "..."},
              "source": "qs1", "report_id": "QR-1", "trace_id": "tr-1"})
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from gateway import inbox_gateway
import logging

__all__ = [
    "ALLOWED_SOURCES",
    "BASE_PATH",
    "CHANNEL_NAMESPACE",
    "ENABLE_ENV",
    "ENDPOINTS",
    "ERROR_HTTP_STATUS",
    "GATEWAY_KINDS",
    "IDEMPOTENCY_HEADER",
    "KIND_TO_GATEWAY",
    "SOURCE_HEADER",
    "STORE_ROOT_ENV",
    "TOKEN_ENV",
    "TOKEN_FILE_ENV",
    "TOKEN_HEADER",
    "W4_STATES",
    "enabled",
    "handle",
    "http_status_for_error",
    "set_store",
    "token_configured",
]

# ---------------------------------------------------------------- 契约常量
# 下面这些常量与 CH2 `osleg_http_contract.py` 逐项对齐；本模块不复用其代码
# （agent_platform 不反向依赖 quant_backtest），一致性与响应信封兼容性由
# `agent_platform/tests/test_osleg_http_api.py` 的对照断言守住。
TOKEN_HEADER = "X-OSLEG-Auth"
SOURCE_HEADER = "X-OSLEG-Source"
IDEMPOTENCY_HEADER = "X-Idempotency-Key"
CONTENT_TYPE_HEADER = "Content-Type"
JSON_CONTENT_TYPE = "application/json"

BASE_PATH = "/osleg/v1"
CHANNEL_NAMESPACE = "http8795"

ALLOWED_SOURCES = ("qs1", "a0")
SOURCE_WHITELIST = ALLOWED_SOURCES

STATE_SUBMITTED = "submitted"
STATE_ACCEPTED = "accepted"
STATE_CONSUMED = "consumed"
STATE_ANSWERED = "answered"
STATE_DELIVERED = "delivered"
STATE_REJECTED = "rejected"
STATE_EXPIRED = "expired"
STATE_UNKNOWN = "unknown"

W4_STATES = (
    STATE_SUBMITTED,
    STATE_ACCEPTED,
    STATE_CONSUMED,
    STATE_ANSWERED,
    STATE_DELIVERED,
    STATE_REJECTED,
    STATE_EXPIRED,
)
ALIVE_STATES = (STATE_SUBMITTED, STATE_ACCEPTED, STATE_CONSUMED, STATE_ANSWERED)

METHOD_GET = "GET"
METHOD_POST = "POST"

# W4 业务 kind -> gateway 投递层 kind 显式映射（与通道① CH1 `KIND_TO_GATEWAY`
# 同口径）。未登记的 kind 一律失败关闭，不猜测、不降级；契约层保留原始 kind。
KIND_TO_GATEWAY = {
    "stock_consult_result": "question",
    "risk_verdict": "task_request",
    "task_request": "task_request",
    "question": "question",
    "feedback": "feedback",
}
GATEWAY_KINDS = ("task_request", "question", "feedback")

# 与 CH2 `ERROR_HTTP_STATUS` 对齐；额外错误码走 `_EXTRA_ERROR_HTTP`（下表之后）。
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

# 本层新增错误码（不进 CH2 表，保持对照表与契约逐字一致）。
_EXTRA_ERROR_HTTP = {
    "token_invalid": 401,
    "token_unconfigured": 503,
    "store_unconfigured": 503,
    "store_unavailable": 503,
    "kind_not_bridged": 422,
    "bad_answer_evidence": 422,
    "bad_delivery_evidence": 422,
    "actor_mismatch": 409,
    "ambiguous_report_id": 409,
    "gateway_error": 502,
    "internal_error": 500,
    "sidecar_missing": 500,
    "store_diverged": 500,
    "idempotency_persist_failed": 500,
}

DEFAULT_HTTP_ERRORS = {
    400: "invalid_request",
    401: "token_missing",
    403: "source_forbidden",
    404: "not_found",
    409: "idempotency_conflict",
    410: "expired",
    422: "invalid_request",
    503: "service_unavailable",
}

# gateway ok=False 错误码 -> (HTTP, W4 错误码, 状态字)；未命中的一律 502。
_GATEWAY_ERROR_TO_API = {
    "bad_id": (422, "invalid_request", STATE_REJECTED),
    "bad_kind": (422, "invalid_request", STATE_REJECTED),
    "bad_payload": (422, "invalid_request", STATE_REJECTED),
    "bad_idempotency_key": (422, "invalid_request", STATE_REJECTED),
    "bad_answer_evidence": (422, "bad_answer_evidence", STATE_REJECTED),
    "bad_delivery_evidence": (422, "bad_delivery_evidence", STATE_REJECTED),
    "idempotency_conflict": (409, "idempotency_conflict", STATE_REJECTED),
    "not_answered": (409, "state_conflict", STATE_REJECTED),
}

# 占位/默认凭据一律不算"已配置"：配成这些值等同未配置 -> 503 失败关闭。
_PLACEHOLDER_TOKENS = frozenset(
    {
        "changeme",
        "change-me",
        "change_me",
        "default",
        "default-token",
        "token",
        "secret",
        "password",
        "test",
        "demo",
        "example",
        "xxx",
    }
)

# 配置入口（全部只读 env / 显式注入，绝无默认凭据）
TOKEN_ENV = "OSLEG_HTTP_TOKEN"
TOKEN_FILE_ENV = "OSLEG_HTTP_TOKEN_FILE"
STORE_ROOT_ENV = "OSLEG_HTTP_STORE_ROOT"
ENABLE_ENV = "OSLEG_HTTP_API"
_ENABLE_TRUTHY = frozenset({"1", "true", "yes", "on"})

GATEWAY_DIR = Path(__file__).resolve().parent
DEFAULT_TOKEN_FILE = GATEWAY_DIR / ".osleg_http_token"

# sidecar 目录名（固定落 store 根下，和 gateway 的 inbox/processed 并列）
SIDECAR_DIRNAME = "osleg_http"
RECORDS_DIRNAME = "records"
IDEM_FILENAME = "idem.json"

_ID_RE_TEXT = r"^[A-Za-z0-9_-]{1,64}$"
# M2（K-ORG-20261031-APIHARDEN）：msg_id 必须全匹配该正则，禁止路径穿越/绝对路径。
_ID_RE = re.compile(_ID_RE_TEXT)

_LOCK = threading.RLock()
_DEFAULT_STORE = None


def http_status_for_error(error: str) -> int:
    """错误码 -> HTTP 状态码；未知错误码按 400 失败关闭。"""
    name = str(error or "").strip()
    if name in ERROR_HTTP_STATUS:
        return ERROR_HTTP_STATUS[name]
    return _EXTRA_ERROR_HTTP.get(name, 400)


# ---------------------------------------------------------------- 端点声明
@dataclass(frozen=True)
class EndpointSpec:
    """通道②单个端点的静态声明（与 CH2 `EndpointSchema` 同字段口径）。"""

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
    "submit": EndpointSpec(
        name="submit",
        method=METHOD_POST,
        path=BASE_PATH + "/submit",
        required_headers=(TOKEN_HEADER, SOURCE_HEADER, IDEMPOTENCY_HEADER),
        required_body=("kind", "payload", "source", "report_id", "trace_id"),
        success_http=201,
        success_state=STATE_SUBMITTED,
    ),
    "consume": EndpointSpec(
        name="consume",
        method=METHOD_POST,
        path=BASE_PATH + "/consume",
        required_headers=(TOKEN_HEADER, SOURCE_HEADER, IDEMPOTENCY_HEADER),
        # 契约 §三 + gateway 证据门槛：答复证据 answered_by/answer_ref 必填
        # （CH2 请求体只有 msg_id/consumed_by/trace_id，属已有缺口，见模块说明）。
        required_body=(
            "msg_id",
            "consumed_by",
            "answered_by",
            "answer_ref",
            "trace_id",
        ),
        success_http=200,
        success_state=STATE_CONSUMED,
    ),
    "answer": EndpointSpec(
        name="answer",
        method=METHOD_POST,
        path=BASE_PATH + "/answer",
        required_headers=(TOKEN_HEADER, SOURCE_HEADER, IDEMPOTENCY_HEADER),
        required_body=("msg_id", "answered_by", "text", "report_id", "trace_id"),
        success_http=200,
        success_state=STATE_ANSWERED,
    ),
    "deliver": EndpointSpec(
        name="deliver",
        method=METHOD_POST,
        path=BASE_PATH + "/deliver",
        required_headers=(TOKEN_HEADER, SOURCE_HEADER, IDEMPOTENCY_HEADER),
        required_body=("msg_id", "channel", "delivered_ref", "trace_id"),
        success_http=200,
        success_state=STATE_DELIVERED,
    ),
    "receipt": EndpointSpec(
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

_PATH_TO_ENDPOINT = {spec.path: spec.name for spec in ENDPOINTS.values()}


# ---------------------------------------------------------------- 小工具
def _str(value) -> str:
    return "" if value is None else str(value).strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value) -> str:
    """请求体/负载的确定性摘要（幂等判定用，不做任何业务解释）。"""
    try:
        canonical = _canonical(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("body 必须可 JSON 序列化: %s" % exc) from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_headers(headers) -> dict:
    """头字段名大小写不敏感；支持 dict / email.message.Message 等带 items 的对象。"""
    out = {}
    if headers is None:
        return out
    items = headers.items() if hasattr(headers, "items") else ()
    for key, value in items:
        name = _str(key).lower()
        if not name:
            continue
        out[name] = "" if value is None else str(value)
    return out


def _normalize_query(query) -> dict:
    out = {}
    if query is None:
        return out
    items = query.items() if hasattr(query, "items") else ()
    for key, value in items:
        name = _str(key)
        if not name:
            continue
        if isinstance(value, (list, tuple)):
            value = value[-1] if value else ""
        out[name] = _str(value)
    return out


def _atomic_write_json(path: Path, data) -> None:
    """tmp + os.replace 原子写（与 inbox_gateway / CH1 同款，崩溃不留半文件）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(".%s.tmp-%s" % (path.name, uuid.uuid4().hex[:8]))
    try:
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, sort_keys=True, indent=1),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/gateway/osleg_http_api.py", exc)
        raise


def _read_json(path: Path):
    """容错读 JSON：损坏/非对象返回 None（调用方按无账处理，不静默造默认值）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------- 配置读取
def enabled() -> bool:
    """env 开关 `OSLEG_HTTP_API`（serve.py 仅在开启时路由本模块）。"""
    return os.environ.get(ENABLE_ENV, "").strip().lower() in _ENABLE_TRUTHY


def _token_file_path() -> Path:
    override = os.environ.get(TOKEN_FILE_ENV, "").strip()
    return Path(override) if override else DEFAULT_TOKEN_FILE


def _read_token_file() -> str:
    path = _token_file_path()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    token = text.strip()
    if not token or token.lower() in _PLACEHOLDER_TOKENS:
        return ""
    return token


def _configured_token() -> str:
    """token 注入解析：env 优先，其次 token 文件；占位符/空值视为未配置。"""
    env_token = os.environ.get(TOKEN_ENV, "").strip()
    if env_token:
        return "" if env_token.lower() in _PLACEHOLDER_TOKENS else env_token
    return _read_token_file()


def token_configured() -> bool:
    """token 是否已由 env/文件注入（未配置时端点 503 失败关闭）。"""
    return bool(_configured_token())


# ---------------------------------------------------------------- store 注入
class _HttpStore:
    """同一 store 根下的 sidecar 句柄（不改变 gateway 自身账结构）。"""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.sidecar = self.root / SIDECAR_DIRNAME
        self.records = self.sidecar / RECORDS_DIRNAME
        self.idem_path = self.sidecar / IDEM_FILENAME

    def ensure(self):
        self.records.mkdir(parents=True, exist_ok=True)
        return self


def set_store(root) -> None:
    """注入 store 根（测试用隔离 tmp）；同时把 gateway store 绑到同一根。

    传 None 只解绑本模块（gateway 全局 store 需调用方自行复位，测试里直接
    `inbox_gateway._DEFAULT_STORE = None`）。未注入时端点 503 失败关闭。
    """
    global _DEFAULT_STORE
    if root is None:
        _DEFAULT_STORE = None
        return
    store = _HttpStore(Path(root)).ensure()
    _DEFAULT_STORE = store
    inbox_gateway.set_store(store.root)


def _active_store():
    """当前 store；env `OSLEG_HTTP_STORE_ROOT` 是显式的生产/隔离注入口。"""
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        env_root = os.environ.get(STORE_ROOT_ENV, "").strip()
        if env_root:
            set_store(env_root)
    return _DEFAULT_STORE


def _store():
    """测试/内部便捷入口（未配置时返回 None，由调用方失败关闭）。"""
    return _active_store()


# ---------------------------------------------------------------- sidecar 账
def _valid_msg_id(msg_id) -> bool:
    """M2（K-ORG-20261031-APIHARDEN）：msg_id 只允许 _ID_RE_TEXT 全匹配。

    旧实现把用户可控 msg_id 直接拼路径（`../`、绝对路径、超长/编码串可穿越），
    审计 v2 M2 要求全匹配 + resolve 包含性双闸；任一不满足即失败关闭。
    """
    text = _str(msg_id)
    return bool(text) and bool(_ID_RE.fullmatch(text))


def _record_path(store, msg_id: str):
    """记录文件路径；非法 msg_id 返回 None（M2 全匹配 + resolve 包含性校验）。"""
    text = _str(msg_id)
    if not _valid_msg_id(text):
        return None
    records_root = store.records.resolve()
    path = (records_root / ("%s.json" % text)).resolve()
    try:
        path.relative_to(records_root)
    except ValueError:
        return None
    return path


def _get_record(store, msg_id: str):
    text = _str(msg_id)
    if not _valid_msg_id(text):
        return None
    path = _record_path(store, text)
    if path is None or not path.is_file():
        return None
    return _read_json(path)


def _save_record(store, record: dict) -> None:
    msg_id = _str(record.get("msg_id"))
    path = _record_path(store, msg_id)
    if path is None:
        # 失败关闭：不写 records/ 之外的任何路径（也不静默改名字典）。
        raise ValueError("invalid msg_id for sidecar record: %r" % (msg_id,))
    _atomic_write_json(path, record)


def _find_records_by_report(store, report_id: str) -> list:
    """按 report_id 找 sidecar 记录（可能多条：同报告多次提交，按 msg_id 排序）。"""
    target = _str(report_id)
    if not target or not store.records.is_dir():
        return []
    matched = []
    for path in sorted(store.records.glob("*.json")):
        record = _read_json(path)
        if record is not None and _str(record.get("report_id")) == target:
            matched.append(record)
    return matched


def _idem_key(endpoint: str, key: str) -> str:
    return "%s|%s" % (endpoint, key)


def _idem_lookup(store, endpoint: str, key: str):
    data = _read_json(store.idem_path) or {}
    entry = data.get(_idem_key(endpoint, key))
    return entry if isinstance(entry, dict) else None


def _idem_save(
    store, endpoint: str, key: str, digest: str, http_status: int, body: dict
) -> None:
    data = _read_json(store.idem_path) or {}
    data[_idem_key(endpoint, key)] = {
        "digest": digest,
        "http_status": int(http_status),
        "body": dict(body),
        "msg_id": _str(body.get("msg_id")),
        "recorded_at": _now_iso(),
    }
    _atomic_write_json(store.idem_path, data)


# ---------------------------------------------------------------- 响应信封
def _failure(
    endpoint: str,
    error: str,
    status_word: str,
    http_status: int,
    *,
    msg_id: str = "",
    report_id: str = "",
    trace_id: str = "",
    **extra,
) -> dict:
    """失败关闭响应体：ok=False + accounted=False，不落账（与 CH2 同口径）。"""
    body = {
        "ok": False,
        "status": str(status_word or STATE_REJECTED),
        "state": str(status_word or STATE_REJECTED),
        "error": _str(error),
        "http_status": int(http_status),
        "endpoint": _str(endpoint),
        "accounted": False,
        "retryable": 500 <= int(http_status) < 600,
        "msg_id": _str(msg_id),
        "report_id": _str(report_id),
        "trace_id": _str(trace_id),
    }
    body.update(extra)
    return body


def _success(
    endpoint: str,
    state: str,
    msg_id: str,
    report_id: str,
    trace_id: str,
    http_status: int,
    **extra,
) -> dict:
    """成功响应体：字段与 CH2 `parse_*_response` 兼容，不冒充已送达/已读。"""
    body = {
        "ok": True,
        "status": str(state),
        "state": str(state),
        "msg_id": _str(msg_id),
        "report_id": _str(report_id),
        "trace_id": _str(trace_id),
        "channel": CHANNEL_NAMESPACE,
        "accounted": True,
        "endpoint": _str(endpoint),
        "http_status": int(http_status),
        "duplicate": False,
        "replayed": False,
        "linked": False,
        "alive": state in ALIVE_STATES,
        "answered": state in (STATE_ANSWERED, STATE_DELIVERED),
        "delivered": state == STATE_DELIVERED,
    }
    body.update(extra)
    return body


# ---------------------------------------------------------------- gateway 视图
def _gateway_error_code(env) -> str:
    error = env.get("error") or {}
    return _str(error.get("code")) or "gateway_failed"


def _gateway_view(msg_id: str) -> dict:
    """gateway 账只读视图（调用前必须已绑定隔离 store）。"""
    env = inbox_gateway.receipt(msg_id)
    if not env.get("ok"):
        return {"ok": False, "error": _gateway_error_code(env)}
    data = env.get("data") or {}
    record = data.get("record") or {}
    return {
        "ok": True,
        "state": _str(data.get("state")) or "not_found",
        "answered": bool(data.get("answered")),
        "delivered": bool(data.get("delivered")),
        "record": dict(record) if isinstance(record, dict) else {},
    }


def _gateway_failure(
    endpoint: str, env, *, msg_id: str = "", report_id: str = "", trace_id: str = ""
):
    """gateway 失败 -> 本层错误码/HTTP 映射（已知校验错误转 4xx，其余 502）。"""
    code = _gateway_error_code(env)
    mapped = _GATEWAY_ERROR_TO_API.get(code)
    if mapped is None:
        http_status, error, word = 502, "gateway_error", STATE_UNKNOWN
    else:
        http_status, error, word = mapped
    return http_status, _failure(
        endpoint,
        error,
        word,
        http_status,
        msg_id=msg_id,
        report_id=report_id,
        trace_id=trace_id,
        gateway_error=code,
    )


def _gateway_idempotency_key(endpoint: str, http_key: str) -> str:
    """HTTP 幂等键 -> gateway 合法幂等键（gateway 只认 ^[A-Za-z0-9_-]{1,64}$）。"""
    raw = ("%s|%s" % (endpoint, http_key)).encode("utf-8")
    return "osleg-" + hashlib.sha256(raw).hexdigest()[:32]


def _gateway_data(env) -> dict:
    data = env.get("data") or {}
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------- 端点实现
def _handle_submit(store, headers, body, http_idem):
    trace_id = _str(body.get("trace_id"))
    report_id = _str(body.get("report_id"))
    if not trace_id:
        # 契约 §六 N5：trace_id 缺失 -> unknown，禁止回填默认值、不落账。
        return 422, _failure(
            "submit", "trace_id_missing", STATE_UNKNOWN, 422, report_id=report_id
        )
    if not report_id:
        return 422, _failure(
            "submit", "report_id_missing", STATE_REJECTED, 422, trace_id=trace_id
        )
    kind = _str(body.get("kind"))
    source = _str(body.get("source"))
    payload = body.get("payload")
    if not kind or not isinstance(payload, dict):
        return 422, _failure(
            "submit",
            "invalid_request",
            STATE_REJECTED,
            422,
            report_id=report_id,
            trace_id=trace_id,
        )
    if source not in ALLOWED_SOURCES:
        return 403, _failure(
            "submit",
            "source_forbidden",
            STATE_REJECTED,
            403,
            report_id=report_id,
            trace_id=trace_id,
        )
    auth_source = _str(headers.get(SOURCE_HEADER.lower()))
    if auth_source and source != auth_source:
        return 403, _failure(
            "submit",
            "source_mismatch",
            STATE_REJECTED,
            403,
            report_id=report_id,
            trace_id=trace_id,
        )
    gateway_kind = KIND_TO_GATEWAY.get(kind)
    if gateway_kind is None:
        return 422, _failure(
            "submit",
            "kind_not_bridged",
            STATE_REJECTED,
            422,
            report_id=report_id,
            trace_id=trace_id,
            kind=kind,
        )
    try:
        payload_digest = _digest(payload)
    except ValueError:
        return 422, _failure(
            "submit",
            "invalid_request",
            STATE_REJECTED,
            422,
            report_id=report_id,
            trace_id=trace_id,
        )

    gw_env = inbox_gateway.submit(
        gateway_kind,
        payload,
        source=source,
        idempotency_key=_gateway_idempotency_key("submit", http_idem),
    )
    if not gw_env.get("ok"):
        return _gateway_failure(
            "submit", gw_env, report_id=report_id, trace_id=trace_id
        )
    gw_data = _gateway_data(gw_env)
    msg_id = _str(gw_data.get("id"))
    if not msg_id:
        return 502, _failure(
            "submit",
            "gateway_error",
            STATE_UNKNOWN,
            502,
            report_id=report_id,
            trace_id=trace_id,
            gateway_error="gateway_msg_id_missing",
        )
    record = _get_record(store, msg_id)
    if record is None:
        if bool(gw_data.get("replayed")):
            # gateway 幂等键命中但 sidecar 丢失：无法恢复 report_id/trace_id，
            # 失败关闭（不猜、不补默认值）。
            return 500, _failure(
                "submit",
                "sidecar_missing",
                STATE_UNKNOWN,
                500,
                msg_id=msg_id,
                report_id=report_id,
                trace_id=trace_id,
            )
        record = {
            "msg_id": msg_id,
            "channel": CHANNEL_NAMESPACE,
            "kind": kind,
            "gateway_kind": gateway_kind,
            "source": source,
            "report_id": report_id,
            "trace_id": trace_id,
            "payload_digest": payload_digest,
            "state": STATE_SUBMITTED,
            "submitted_at": _now_iso(),
            "duplicates": 0,
            "consumed_at": "",
            "consumed_by": "",
            "answered_by": "",
            "answer_ref": "",
            "answered_at": "",
            "answer_text": "",
            "delivered_at": "",
            "delivered_channel": "",
            "delivered_ref": "",
            "http_idempotency_key": http_idem,
        }
        _save_record(store, record)
        return 201, _success(
            "submit",
            STATE_SUBMITTED,
            msg_id,
            report_id,
            trace_id,
            201,
            kind=kind,
            gateway_kind=gateway_kind,
            source=source,
        )
    # 崩溃恢复：gateway 幂等回放 + sidecar 已在 -> 按当前 W4 状态回放。
    return 200, _success(
        "submit",
        _str(record.get("state")) or STATE_SUBMITTED,
        msg_id,
        report_id,
        trace_id,
        200,
        kind=kind,
        gateway_kind=gateway_kind,
        source=source,
        replayed=True,
        duplicate=True,
    )


def _handle_consume(store, headers, body, http_idem):
    msg_id = _str(body.get("msg_id"))
    trace_id = _str(body.get("trace_id"))
    report_id = _str(body.get("report_id"))
    if not trace_id:
        return 422, _failure(
            "consume",
            "trace_id_missing",
            STATE_UNKNOWN,
            422,
            msg_id=msg_id,
            report_id=report_id,
        )
    if not msg_id:
        return 422, _failure(
            "consume", "invalid_request", STATE_REJECTED, 422, trace_id=trace_id
        )
    record = _get_record(store, msg_id)
    if record is None:
        return 404, _failure(
            "consume",
            "not_found",
            STATE_REJECTED,
            404,
            msg_id=msg_id,
            trace_id=trace_id,
        )
    record_report = _str(record.get("report_id"))
    record_trace = _str(record.get("trace_id"))
    if trace_id != record_trace:
        return 422, _failure(
            "consume",
            "trace_mismatch",
            STATE_UNKNOWN,
            422,
            msg_id=msg_id,
            report_id=record_report,
        )
    if report_id and report_id != record_report:
        return 422, _failure(
            "consume",
            "report_id_mismatch",
            STATE_REJECTED,
            422,
            msg_id=msg_id,
            report_id=record_report,
            trace_id=record_trace,
        )
    consumed_by = _str(body.get("consumed_by"))
    answered_by = _str(body.get("answered_by"))
    answer_ref = _str(body.get("answer_ref"))
    if not consumed_by:
        return 422, _failure(
            "consume",
            "invalid_request",
            STATE_REJECTED,
            422,
            msg_id=msg_id,
            report_id=record_report,
            trace_id=record_trace,
        )
    if not answered_by or not answer_ref:
        # gateway 证据门槛原样保留（缺 answered_by/answer_ref 不推进任何状态）。
        return 422, _failure(
            "consume",
            "bad_answer_evidence",
            STATE_REJECTED,
            422,
            msg_id=msg_id,
            report_id=record_report,
            trace_id=record_trace,
        )

    state = _str(record.get("state"))
    if state == STATE_CONSUMED:
        return 200, _success(
            "consume",
            STATE_CONSUMED,
            msg_id,
            record_report,
            record_trace,
            200,
            consumed_by=consumed_by,
            answered_by=answered_by,
            replayed=True,
            duplicate=True,
        )
    if state != STATE_SUBMITTED:
        return 409, _failure(
            "consume",
            "state_conflict",
            STATE_REJECTED,
            409,
            msg_id=msg_id,
            report_id=record_report,
            trace_id=record_trace,
            state=state,
        )

    gw_env = inbox_gateway.consume(
        msg_id, answered_by=answered_by, answer_ref=answer_ref
    )
    if not gw_env.get("ok"):
        return _gateway_failure(
            "consume",
            gw_env,
            msg_id=msg_id,
            report_id=record_report,
            trace_id=record_trace,
        )
    if _str(_gateway_data(gw_env).get("state")) == "not_found":
        return 404, _failure(
            "consume",
            "not_found",
            STATE_REJECTED,
            404,
            msg_id=msg_id,
            report_id=record_report,
            trace_id=record_trace,
        )

    record.update(
        {
            "state": STATE_CONSUMED,
            "consumed_at": _now_iso(),
            "consumed_by": consumed_by,
            "answered_by": answered_by,
            "answer_ref": answer_ref,
        }
    )
    _save_record(store, record)
    return 200, _success(
        "consume",
        STATE_CONSUMED,
        msg_id,
        record_report,
        record_trace,
        200,
        consumed_by=consumed_by,
        answered_by=answered_by,
        answer_ref=answer_ref,
    )


def _handle_answer(store, headers, body, http_idem):
    msg_id = _str(body.get("msg_id"))
    trace_id = _str(body.get("trace_id"))
    report_id = _str(body.get("report_id"))
    answered_by = _str(body.get("answered_by"))
    raw_text = body.get("text")
    text = raw_text.strip() if isinstance(raw_text, str) else ""
    if not trace_id:
        return 422, _failure(
            "answer",
            "trace_id_missing",
            STATE_UNKNOWN,
            422,
            msg_id=msg_id,
            report_id=report_id,
        )
    if not msg_id:
        return 422, _failure(
            "answer", "invalid_request", STATE_REJECTED, 422, trace_id=trace_id
        )
    record = _get_record(store, msg_id)
    if record is None:
        return 404, _failure(
            "answer", "not_found", STATE_REJECTED, 404, msg_id=msg_id, trace_id=trace_id
        )
    record_report = _str(record.get("report_id"))
    record_trace = _str(record.get("trace_id"))
    if trace_id != record_trace:
        return 422, _failure(
            "answer",
            "trace_mismatch",
            STATE_UNKNOWN,
            422,
            msg_id=msg_id,
            report_id=record_report,
        )
    if not report_id:
        # 契约 §六 N4：答复缺 report_id -> 拒收、linked=False、不写 answered。
        return 422, _failure(
            "answer",
            "report_id_missing",
            STATE_REJECTED,
            422,
            msg_id=msg_id,
            trace_id=record_trace,
            linked=False,
        )
    if report_id != record_report:
        return 422, _failure(
            "answer",
            "report_id_mismatch",
            STATE_REJECTED,
            422,
            msg_id=msg_id,
            trace_id=record_trace,
            linked=False,
        )
    if not answered_by or not text:
        return 422, _failure(
            "answer",
            "invalid_request",
            STATE_REJECTED,
            422,
            msg_id=msg_id,
            report_id=record_report,
            trace_id=record_trace,
        )

    state = _str(record.get("state"))
    if state in (STATE_ANSWERED, STATE_DELIVERED):
        # 首胜语义：答复已落账，重复答复只回放、不改写正文（幂等）。
        return 200, _success(
            "answer",
            state,
            msg_id,
            record_report,
            record_trace,
            200,
            answered_by=answered_by,
            replayed=True,
            duplicate=True,
            linked=True,
        )
    if state != STATE_CONSUMED:
        return 409, _failure(
            "answer",
            "state_conflict",
            STATE_REJECTED,
            409,
            msg_id=msg_id,
            report_id=record_report,
            trace_id=record_trace,
            state=state,
        )

    gateway = _gateway_view(msg_id)
    if not gateway.get("ok"):
        return _gateway_failure(
            "answer",
            gateway,
            msg_id=msg_id,
            report_id=record_report,
            trace_id=record_trace,
        )
    if gateway.get("state") == "not_found" or not gateway.get("answered"):
        return 409, _failure(
            "answer",
            "state_conflict",
            STATE_REJECTED,
            409,
            msg_id=msg_id,
            report_id=record_report,
            trace_id=record_trace,
            gateway_error="gateway_answer_evidence_missing",
        )
    if _str((gateway.get("record") or {}).get("answered_by")) != answered_by:
        return 409, _failure(
            "answer",
            "actor_mismatch",
            STATE_REJECTED,
            409,
            msg_id=msg_id,
            report_id=record_report,
            trace_id=record_trace,
            gateway_error="answered_by_mismatch",
        )

    record.update(
        {
            "state": STATE_ANSWERED,
            "answered_at": _now_iso(),
            "answer_text": text,
            "answered_by": answered_by,
            "answer_report_id": record_report,
            "answer_trace_id": record_trace,
        }
    )
    _save_record(store, record)
    return 200, _success(
        "answer",
        STATE_ANSWERED,
        msg_id,
        record_report,
        record_trace,
        200,
        answered_by=answered_by,
        linked=True,
    )


def _handle_deliver(store, headers, body, http_idem):
    msg_id = _str(body.get("msg_id"))
    trace_id = _str(body.get("trace_id"))
    report_id = _str(body.get("report_id"))
    channel = _str(body.get("channel"))
    delivered_ref = _str(body.get("delivered_ref"))
    if not trace_id:
        return 422, _failure(
            "deliver",
            "trace_id_missing",
            STATE_UNKNOWN,
            422,
            msg_id=msg_id,
            report_id=report_id,
        )
    if not msg_id:
        return 422, _failure(
            "deliver", "invalid_request", STATE_REJECTED, 422, trace_id=trace_id
        )
    record = _get_record(store, msg_id)
    if record is None:
        return 404, _failure(
            "deliver",
            "not_found",
            STATE_REJECTED,
            404,
            msg_id=msg_id,
            trace_id=trace_id,
        )
    record_report = _str(record.get("report_id"))
    record_trace = _str(record.get("trace_id"))
    if trace_id != record_trace:
        return 422, _failure(
            "deliver",
            "trace_mismatch",
            STATE_UNKNOWN,
            422,
            msg_id=msg_id,
            report_id=record_report,
        )
    if report_id and report_id != record_report:
        return 422, _failure(
            "deliver",
            "report_id_mismatch",
            STATE_REJECTED,
            422,
            msg_id=msg_id,
            report_id=record_report,
            trace_id=record_trace,
        )

    state = _str(record.get("state"))
    if state == STATE_DELIVERED:
        return 200, _success(
            "deliver",
            STATE_DELIVERED,
            msg_id,
            record_report,
            record_trace,
            200,
            delivered_channel=_str(record.get("delivered_channel")),
            replayed=True,
            duplicate=True,
        )
    if state != STATE_ANSWERED:
        # gateway 门槛外的前置：必须先 answered（先回传后答复=伪造交付）。
        return 409, _failure(
            "deliver",
            "state_conflict",
            STATE_REJECTED,
            409,
            msg_id=msg_id,
            report_id=record_report,
            trace_id=record_trace,
            state=state,
        )
    if not channel or not delivered_ref:
        return 422, _failure(
            "deliver",
            "bad_delivery_evidence",
            STATE_REJECTED,
            422,
            msg_id=msg_id,
            report_id=record_report,
            trace_id=record_trace,
        )

    gw_env = inbox_gateway.deliver(msg_id, channel=channel, delivered_ref=delivered_ref)
    if not gw_env.get("ok"):
        return _gateway_failure(
            "deliver",
            gw_env,
            msg_id=msg_id,
            report_id=record_report,
            trace_id=record_trace,
        )
    if _str(_gateway_data(gw_env).get("state")) == "not_found":
        return 404, _failure(
            "deliver",
            "not_found",
            STATE_REJECTED,
            404,
            msg_id=msg_id,
            report_id=record_report,
            trace_id=record_trace,
        )

    record.update(
        {
            "state": STATE_DELIVERED,
            "delivered_at": _now_iso(),
            "delivered_channel": channel,
            "delivered_ref": delivered_ref,
        }
    )
    _save_record(store, record)
    return 200, _success(
        "deliver",
        STATE_DELIVERED,
        msg_id,
        record_report,
        record_trace,
        200,
        delivered_channel=channel,
        delivered_ref=delivered_ref,
    )


def _receipt_record_view(record: dict, gateway: dict) -> dict:
    state = _str(record.get("state")) or STATE_UNKNOWN
    delivered = state == STATE_DELIVERED
    answered = state in (STATE_ANSWERED, STATE_DELIVERED)
    answer_report_id = _str(record.get("answer_report_id"))
    answer_trace_id = _str(record.get("answer_trace_id"))
    linked = bool(
        answer_report_id
        and answer_report_id == _str(record.get("report_id"))
        and answer_trace_id == _str(record.get("trace_id"))
    )
    return {
        "msg_id": _str(record.get("msg_id")),
        "channel": CHANNEL_NAMESPACE,
        "kind": _str(record.get("kind")),
        "state": state,
        "status": state,
        "report_id": _str(record.get("report_id")),
        "trace_id": _str(record.get("trace_id")),
        "alive": state in ALIVE_STATES,
        "answered": answered,
        "delivered": delivered,
        "linked": linked,
        "duplicate": int(record.get("duplicates") or 0) > 0,
        "consumed_by": _str(record.get("consumed_by")),
        "answered_by": _str(record.get("answered_by")),
        "delivered_channel": _str(record.get("delivered_channel")),
        "submitted_at": _str(record.get("submitted_at")),
        "consumed_at": _str(record.get("consumed_at")),
        "answered_at": _str(record.get("answered_at")),
        "delivered_at": _str(record.get("delivered_at")),
        "gateway_state": _str(gateway.get("state")),
        "gateway_answered": bool(gateway.get("answered")),
        "gateway_delivered": bool(gateway.get("delivered")),
    }


def _handle_receipt(store, headers, query):
    msg_id = _str(query.get("msg_id"))
    report_id = _str(query.get("report_id"))
    trace_id = _str(query.get("trace_id"))
    if not msg_id and not report_id:
        return 422, _failure("receipt", "invalid_request", STATE_REJECTED, 422)
    if msg_id:
        record = _get_record(store, msg_id)
        matched = [record] if record is not None else []
    else:
        matched = _find_records_by_report(store, report_id)
    if not matched:
        if msg_id and _valid_msg_id(msg_id):
            # M2：只有合法 msg_id 才允许回查 gateway 账（防路径穿越查询）。
            gateway = _gateway_view(msg_id)
            if gateway.get("ok") and gateway.get("state") != "not_found":
                return 500, _failure(
                    "receipt",
                    "sidecar_missing",
                    STATE_UNKNOWN,
                    500,
                    msg_id=msg_id,
                    report_id=report_id,
                )
        return 404, _failure(
            "receipt",
            "not_found",
            STATE_REJECTED,
            404,
            msg_id=msg_id,
            report_id=report_id,
        )
    if len(matched) > 1:
        # 同 report_id 多条（同报告重复提交）：不静默挑一条，显式要求按 msg_id 查。
        return 409, _failure(
            "receipt",
            "ambiguous_report_id",
            STATE_REJECTED,
            409,
            report_id=report_id,
            msg_ids=[_str(item.get("msg_id")) for item in matched],
        )
    record = matched[0]
    record_msg_id = _str(record.get("msg_id"))
    record_report = _str(record.get("report_id"))
    record_trace = _str(record.get("trace_id"))
    if trace_id and trace_id != record_trace:
        return 422, _failure(
            "receipt",
            "trace_mismatch",
            STATE_UNKNOWN,
            422,
            msg_id=record_msg_id,
            report_id=record_report,
        )
    if report_id and report_id != record_report:
        return 422, _failure(
            "receipt",
            "report_id_mismatch",
            STATE_REJECTED,
            422,
            msg_id=record_msg_id,
            report_id=record_report,
            trace_id=record_trace,
        )

    gateway = _gateway_view(record_msg_id)
    if not gateway.get("ok"):
        return _gateway_failure(
            "receipt",
            gateway,
            msg_id=record_msg_id,
            report_id=record_report,
            trace_id=record_trace,
        )
    if gateway.get("state") == "not_found":
        return 500, _failure(
            "receipt",
            "store_diverged",
            STATE_UNKNOWN,
            500,
            msg_id=record_msg_id,
            report_id=record_report,
            trace_id=record_trace,
            gateway_error="gateway_record_missing",
        )
    state = _str(record.get("state"))
    gateway_state = _str(gateway.get("state"))
    if gateway_state == "processed" and state == STATE_SUBMITTED:
        return 500, _failure(
            "receipt",
            "store_diverged",
            STATE_UNKNOWN,
            500,
            msg_id=record_msg_id,
            report_id=record_report,
            trace_id=record_trace,
            gateway_error="sidecar_behind_gateway",
        )
    if state in (STATE_CONSUMED, STATE_ANSWERED) and not gateway.get("answered"):
        return 500, _failure(
            "receipt",
            "store_diverged",
            STATE_UNKNOWN,
            500,
            msg_id=record_msg_id,
            report_id=record_report,
            trace_id=record_trace,
            gateway_error="gateway_answer_evidence_missing",
        )
    if state == STATE_DELIVERED and not gateway.get("delivered"):
        return 500, _failure(
            "receipt",
            "store_diverged",
            STATE_UNKNOWN,
            500,
            msg_id=record_msg_id,
            report_id=record_report,
            trace_id=record_trace,
            gateway_error="gateway_delivery_evidence_missing",
        )
    if gateway.get("delivered") and state != STATE_DELIVERED:
        return 500, _failure(
            "receipt",
            "store_diverged",
            STATE_UNKNOWN,
            500,
            msg_id=record_msg_id,
            report_id=record_report,
            trace_id=record_trace,
            gateway_error="delivery_ledger_diverged",
        )

    view = _receipt_record_view(record, gateway)
    body = {
        "ok": True,
        "status": view["state"],
        "state": view["state"],
        "accounted": True,
        "endpoint": "receipt",
        "http_status": 200,
        "channel": CHANNEL_NAMESPACE,
        "msg_id": view["msg_id"],
        "report_id": view["report_id"],
        "trace_id": view["trace_id"],
        "alive": view["alive"],
        "answered": view["answered"],
        "delivered": view["delivered"],
        "linked": view["linked"],
        "duplicate": view["duplicate"],
        "records": [view],
        "channels": [CHANNEL_NAMESPACE],
        "msg_ids": [view["msg_id"]],
        "trace_ids": [view["trace_id"]],
        "trace_consistent": True,
        "total": 1,
        "gateway_state": view["gateway_state"],
    }
    return 200, body


_POST_HANDLERS = {
    "submit": _handle_submit,
    "consume": _handle_consume,
    "answer": _handle_answer,
    "deliver": _handle_deliver,
}


# ---------------------------------------------------------------- 鉴权
def _authenticate(endpoint: str, headers: dict, token: str):
    """token + 来源白名单校验；通过返回 None，否则返回 (status, body)。"""
    provided = _str(headers.get(TOKEN_HEADER.lower()))
    if not provided:
        return 401, _failure(endpoint, "token_missing", STATE_REJECTED, 401)
    try:
        matched = hmac.compare_digest(provided.encode("utf-8"), token.encode("utf-8"))
    except (UnicodeError, TypeError):
        matched = False
    if not matched:
        return 401, _failure(endpoint, "token_invalid", STATE_REJECTED, 401)
    source = _str(headers.get(SOURCE_HEADER.lower()))
    if source not in ALLOWED_SOURCES:
        return 403, _failure(endpoint, "source_forbidden", STATE_REJECTED, 403)
    return None


# ---------------------------------------------------------------- 幂等重放
def _replay(store, endpoint: str, digest: str, seen: dict):
    if _str(seen.get("digest")) != digest:
        return 409, _failure(
            endpoint,
            "idempotency_conflict",
            STATE_REJECTED,
            409,
            conflict_with=_str(seen.get("msg_id")),
        )
    payload = dict(seen.get("body") or {})
    payload.update(replayed=True, duplicate=True)
    msg_id = _str(seen.get("msg_id")) or _str(payload.get("msg_id"))
    if endpoint == "submit" and msg_id:
        record = _get_record(store, msg_id)
        if record is not None:
            record["duplicates"] = int(record.get("duplicates") or 0) + 1
            record["last_replayed_at"] = _now_iso()
            _save_record(store, record)
            payload["status"] = _str(record.get("state")) or STATE_SUBMITTED
            payload["state"] = payload["status"]
    return 200, payload


# ---------------------------------------------------------------- 入口
def _handle_post(endpoint: str, store, headers: dict, body):
    if body is None:
        body = {}
    if not isinstance(body, dict):
        return 422, _failure(endpoint, "invalid_request", STATE_REJECTED, 422)
    try:
        digest = _digest(body)
    except ValueError:
        return 422, _failure(endpoint, "invalid_request", STATE_REJECTED, 422)
    http_idem = _str(headers.get(IDEMPOTENCY_HEADER.lower()))
    if not http_idem:
        return 422, _failure(endpoint, "idempotency_key_missing", STATE_REJECTED, 422)
    seen = _idem_lookup(store, endpoint, http_idem)
    if seen is not None:
        return _replay(store, endpoint, digest, seen)
    handler = _POST_HANDLERS[endpoint]
    status, payload = handler(store, headers, body, http_idem)
    if 200 <= int(status) < 300:
        try:
            _idem_save(store, endpoint, http_idem, digest, status, payload)
        except OSError as exc:
            return 500, _failure(
                endpoint,
                "idempotency_persist_failed",
                STATE_UNKNOWN,
                500,
                msg_id=_str(payload.get("msg_id")),
                report_id=_str(payload.get("report_id")),
                trace_id=_str(payload.get("trace_id")),
                detail=str(exc),
            )
    return status, payload


def handle(method, path, headers=None, body=None, query=None):
    """统一入口：返回 (http_status, body_dict)；任何失败都走信封、不抛业务异常。

    - 本函数不检查 env 开关（由 `gateway/serve.py` 决定是否路由；测试可直调）；
    - 未配置 token -> 503；缺失/错误 token -> 401；坏来源 -> 403；
    - 未配置 store -> 503；未知端点/方法 -> 404 `invalid_endpoint`。
    """
    raw_path = _str(path).split("?", 1)[0]
    endpoint = _PATH_TO_ENDPOINT.get(raw_path)
    if endpoint is None:
        return 404, _failure("", "invalid_endpoint", STATE_REJECTED, 404)
    spec = ENDPOINTS[endpoint]
    if _str(method).upper() != spec.method:
        return 404, _failure(endpoint, "invalid_endpoint", STATE_REJECTED, 404)
    token = _configured_token()
    if not token:
        return 503, _failure(endpoint, "token_unconfigured", STATE_UNKNOWN, 503)
    normalized = _normalize_headers(headers)
    failure = _authenticate(endpoint, normalized, token)
    if failure is not None:
        return failure
    try:
        with _LOCK:
            store = _active_store()
            if store is None:
                return 503, _failure(endpoint, "store_unconfigured", STATE_UNKNOWN, 503)
            if endpoint == "receipt":
                return _handle_receipt(store, normalized, _normalize_query(query))
            return _handle_post(endpoint, store, normalized, body)
    except OSError as exc:
        return 503, _failure(
            endpoint, "store_unavailable", STATE_UNKNOWN, 503, detail=str(exc)
        )
    except Exception as exc:  # noqa: BLE001
        return 500, _failure(
            endpoint, "internal_error", STATE_UNKNOWN, 500, detail=type(exc).__name__
        )
