"""统一事件格式和严格 JSON、时间验证。"""

import json
import math
from datetime import datetime, timezone


ENVIRONMENTS = frozenset({"shadow", "paper", "advisory", "legacy"})
TRACE_FIELDS = ("trace_id", "fact_hash", "decision_id", "idempotency_key")
EVENT_FIELDS = frozenset({
    "schema_version", "kind", "environment", "source", "source_id",
    "occurred_at", "available_at", "payload",
})


def canonical_json(value) -> str:
    active = set()

    def check(item):
        if item is None or type(item) in (str, int, bool):
            return
        if type(item) is float:
            if not math.isfinite(item):
                raise ValueError("JSON 不允许 NaN 或无穷数")
            return
        if type(item) not in (dict, list):
            raise ValueError("值必须由标准 JSON 类型构成")
        if id(item) in active:
            raise ValueError("JSON 不允许循环引用")
        active.add(id(item))
        if isinstance(item, dict):
            for key, child in item.items():
                if type(key) is not str:
                    raise ValueError("JSON 对象的键必须是字符串")
                check(child)
        else:
            for child in item:
                check(child)
        active.remove(id(item))

    try:
        check(value)
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (RecursionError, TypeError, OverflowError) as exc:
        raise ValueError("无法生成规范 JSON") from exc


def normalize_time(value) -> str:
    try:
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value)
        elif isinstance(value, datetime):
            parsed = value
        else:
            raise ValueError("时间必须是带时区的 ISO 字符串或 datetime")
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("时间必须包含时区")
        return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")
    except (TypeError, OverflowError, ValueError) as exc:
        raise ValueError("时间非法，必须包含明确时区") from exc


def utc_now() -> str:
    return normalize_time(datetime.now(timezone.utc))


def trace_payload(trace_id=None, fact_hash=None, decision_id=None,
                  idempotency_key=None, **payload) -> dict:
    """返回只含已提供追踪字段的 payload 副本，避免各层自行拼字段。"""
    values = {
        "trace_id": trace_id,
        "fact_hash": fact_hash,
        "decision_id": decision_id,
        "idempotency_key": idempotency_key,
    }
    for name, value in values.items():
        if value is not None:
            if type(value) is not str or not value.strip():
                raise ValueError(f"{name} 必须是非空字符串")
            payload[name] = value
    return json.loads(canonical_json(payload))


def trace_event(kind, environment, source, source_id, occurred_at,
                payload=None, available_at=None, **trace):
    """构造统一事件；追踪字段同时落在事件 payload，旧事件字段不变。"""
    body = dict(payload or {})
    for name in TRACE_FIELDS:
        if name in trace:
            body[name] = trace[name]
    body = trace_payload(**{name: body.pop(name, None) for name in TRACE_FIELDS}, **body)
    return validate_event({
        "schema_version": 1, "kind": kind, "environment": environment,
        "source": source, "source_id": source_id, "occurred_at": occurred_at,
        "available_at": available_at, "payload": body,
    })


def validate_event(event) -> dict:
    if type(event) is not dict:
        raise ValueError("事件必须是 JSON 对象")
    if set(event) - EVENT_FIELDS:
        raise ValueError("事件包含未知字段或调用方伪造的计算字段")
    version = event.get("schema_version", 1)
    if type(version) is not int or version != 1:
        raise ValueError("仅支持 schema_version=1")
    for key in ("kind", "environment", "source", "source_id"):
        if type(event.get(key)) is not str or not event[key].strip():
            raise ValueError(f"{key} 必须是非空字符串")
    if event["environment"] not in ENVIRONMENTS:
        raise ValueError("不支持的事件环境")
    if type(event.get("payload")) is not dict:
        raise ValueError("payload 必须是 JSON 对象")
    result = dict(event)
    result["schema_version"] = version
    result["occurred_at"] = normalize_time(event.get("occurred_at"))
    available = event.get("available_at")
    result["available_at"] = normalize_time(available) if available is not None else None
    return json.loads(canonical_json(result))
