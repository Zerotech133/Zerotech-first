"""把当前行情和日历作为单事件持久接收，保留来源未审核限制。"""

import hashlib
import json
import re
import uuid

from . import contracts
from .provenance import capture_snapshot, validate_snapshot
from .source_inputs import snapshot_for_outcomes
from .trading_calendar import capture_calendar, validate_calendar_capture


_RECEIPT_FIELDS = {"schema_version", "capture_event_id", "stream_id", "previous_event_id",
                   "recorded_available_at", "snapshot", "calendar_capture", "event", "production_eligible"}
_PAYLOAD_FIELDS = {"schema_version", "observation_id", "stream_id", "previous_event_id", "snapshot", "calendar_capture"}
_STORED_FIELDS = contracts.EVENT_FIELDS | {"event_id", "payload_hash", "recorded_at"}
_SOURCE_REQUEST = {"path", "source_id", "code", "price_basis", "revision"}
_CALENDAR_REQUEST = {"paths", "exchange", "source_id", "revision", "coverage_start", "coverage_end"}


def _fields(value, fields, name):
    if type(value) is not dict or set(value) != fields:
        raise ValueError(f"{name} 必须严格包含约定字段")


def _hash(value):
    return hashlib.sha256(contracts.canonical_json(value).encode("utf-8")).hexdigest()


def _digest(value, name):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"{name} 必须是小写 SHA256")
    return value


def _version(value):
    if type(value) is not int or value != 1:
        raise ValueError("仅支持整数 schema_version=1")


def _time(value, name):
    if type(value) is not str:
        raise ValueError(f"{name} 必须是带时区的 ISO 字符串")
    return contracts.normalize_time(value)


def _canonical_time(value, name):
    normalized = _time(value, name)
    if normalized != value:
        raise ValueError(f"{name} 必须是含微秒的规范 UTC 字符串")
    return normalized


def _request(value, required, name):
    contracts.canonical_json(value)
    if type(value) is not dict or not required <= set(value) or set(value) - required - {"format"}:
        raise ValueError(f"{name} 字段必须匹配当前采集请求，不能回填时间或快照")
    return json.loads(contracts.canonical_json(value))


def _stream(snapshot, calendar):
    return _hash({"source_id": snapshot["source_id"], "code": snapshot["code"],
                  "period": snapshot["period"], "price_basis": snapshot["price_basis"],
                  "exchange": calendar["exchange"], "calendar_source_id": calendar["source_id"]})


def _validated_event(event):
    contracts.canonical_json(event)
    _fields(event, _STORED_FIELDS, "完整接收事件")
    base = {name: event[name] for name in contracts.EVENT_FIELDS}
    validated = contracts.validate_event(base)
    if validated != base:
        raise ValueError("接收事件必须使用规范时间和完整字段")
    _digest(event["event_id"], "event_id")
    _digest(event["payload_hash"], "payload_hash")
    if event["event_id"] != _hash(base) or event["payload_hash"] != _hash(event["payload"]):
        raise ValueError("接收事件或 payload 完整性哈希不匹配")
    for name, expected in (("kind", "source.capture"), ("environment", "shadow"),
                           ("source", "ai_operator.capture_journal")):
        if event[name] != expected:
            raise ValueError(f"接收事件的 {name} 身份不符")
    payload = event["payload"]
    _fields(payload, _PAYLOAD_FIELDS, "接收 payload")
    _version(payload["schema_version"])
    if type(payload["observation_id"]) is not str or re.fullmatch(r"[0-9a-f]{32}", payload["observation_id"]) is None:
        raise ValueError("observation_id 必须是 32 位小写十六进制字符串")
    _digest(payload["stream_id"], "stream_id")
    previous = payload["previous_event_id"]
    if previous is not None:
        _digest(previous, "previous_event_id")
        if previous == event["event_id"]:
            raise ValueError("接收事件不能以自身作为前驱")
    snapshot = validate_snapshot(payload["snapshot"])
    calendar = validate_calendar_capture(payload["calendar_capture"])
    if snapshot != payload["snapshot"] or calendar != payload["calendar_capture"]:
        raise ValueError("持久快照必须符合规范契约")
    snapshot_for_outcomes(snapshot, calendar)
    stream_id = _stream(snapshot, calendar)
    if payload["stream_id"] != stream_id or event["source_id"] != stream_id:
        raise ValueError("stream_id 与证券、价格口径和日历来源不匹配")
    received = max(snapshot["received_at"], calendar["received_at"])
    if event["occurred_at"] != received or event["available_at"] != received:
        raise ValueError("接收事件时间必须等于双方实际接收时间的最大值")
    recorded = _canonical_time(event["recorded_at"], "recorded_at")
    if recorded < received or recorded > contracts.utc_now():
        raise ValueError("recorded_at 不能早于实际接收或晚于当前时间")
    return json.loads(contracts.canonical_json(event))


def _receipt(event):
    payload = event["payload"]
    return {"schema_version": 1, "capture_event_id": event["event_id"], "stream_id": payload["stream_id"],
            "previous_event_id": payload["previous_event_id"],
            "recorded_available_at": max(event["recorded_at"], event["occurred_at"]),
            "snapshot": payload["snapshot"], "calendar_capture": payload["calendar_capture"],
            "event": event, "production_eligible": False}


def validate_receipt(receipt) -> dict:
    """离线重验完整事件及快照语义；哈希不认证管理员重造的数据库。"""
    contracts.canonical_json(receipt)
    _fields(receipt, _RECEIPT_FIELDS, "接收回执")
    _version(receipt["schema_version"])
    if receipt["production_eligible"] is not False:
        raise ValueError("接收回执不能启用生产资格")
    event = _validated_event(receipt["event"])
    expected = _receipt(event)
    if contracts.canonical_json(receipt) != contracts.canonical_json(expected):
        raise ValueError("接收回执与完整事件逐项不一致")
    return json.loads(contracts.canonical_json(expected))


def capture_to_store(store, source_request, calendar_request, previous_event_id=None) -> dict:
    """现场读取双快照，在全部预检后通过单次 append_event 持久接收。"""
    store._ensure_writable()
    source_request = _request(source_request, _SOURCE_REQUEST, "行情请求")
    calendar_request = _request(calendar_request, _CALENDAR_REQUEST, "日历请求")
    previous = None
    if previous_event_id is not None:
        _digest(previous_event_id, "previous_event_id")
        previous = load_capture(store, previous_event_id, contracts.utc_now())
    snapshot = validate_snapshot(capture_snapshot(**source_request))
    calendar = validate_calendar_capture(capture_calendar(**calendar_request))
    snapshot_for_outcomes(snapshot, calendar)
    stream_id = _stream(snapshot, calendar)
    received = max(snapshot["received_at"], calendar["received_at"])
    if received > contracts.utc_now():
        raise ValueError("系统时钟倒退，当前时间早于接收时间")
    if previous is not None:
        if previous["stream_id"] != stream_id:
            raise ValueError("前驱必须属于同一来源、证券、口径及日历观察链")
        if min(snapshot["received_at"], calendar["received_at"]) < previous["recorded_available_at"]:
            raise ValueError("系统时钟倒退，双快照接收必须不早于前驱持久可用时间")
    payload = {"schema_version": 1, "observation_id": uuid.uuid4().hex,
               "stream_id": stream_id, "previous_event_id": previous_event_id,
               "snapshot": snapshot, "calendar_capture": calendar}
    event = {"schema_version": 1, "kind": "source.capture", "environment": "shadow",
             "source": "ai_operator.capture_journal", "source_id": stream_id,
             "occurred_at": received, "available_at": received, "payload": payload}
    event_id = store.append_event(event)
    return validate_receipt(_receipt(_validated_event(store.get_event(event_id))))


def load_capture(store, capture_event_id, as_of) -> dict:
    """按持久可用时间读取完整接收事件，不重新访问行情或日历源文件。"""
    _digest(capture_event_id, "capture_event_id")
    instant = _time(as_of, "as_of")
    event = store.get_event(capture_event_id)
    if event is None:
        raise ValueError("持久接收事件不存在")
    receipt = validate_receipt(_receipt(_validated_event(event)))
    if receipt["capture_event_id"] != capture_event_id:
        raise ValueError("读取的接收事件 ID 与请求不一致")
    if receipt["recorded_available_at"] > instant:
        raise ValueError("接收事件在 as_of 时点尚未持久可用")
    return receipt


def capture_history(store, capture_event_id, as_of, limit=20) -> list:
    """返回显式前驱链最近若干项，旧到新；不发现整个库的所有来源。"""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit 必须是 1 至 100 的整数，不能是布尔值")
    _digest(capture_event_id, "capture_event_id")
    instant = _time(as_of, "as_of")
    result, seen = [], set()
    event_id = capture_event_id
    while event_id is not None and len(result) < limit:
        if event_id in seen:
            raise ValueError("接收观察链包含循环")
        seen.add(event_id)
        receipt = load_capture(store, event_id, instant)
        if result:
            successor = result[-1]
            if successor["stream_id"] != receipt["stream_id"]:
                raise ValueError("前驱链包含不同观察来源身份")
            if min(successor["snapshot"]["received_at"], successor["calendar_capture"]["received_at"]) < receipt["recorded_available_at"]:
                raise ValueError("前驱链的接收时间倒退")
        result.append(receipt)
        event_id = receipt["previous_event_id"]
    if event_id in seen:
        raise ValueError("接收观察链包含循环")
    return list(reversed(result))
