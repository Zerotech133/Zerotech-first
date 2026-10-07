"""持久来源审核和时点门槛；文档匹配通过后仍须独立人审。"""

import hashlib
import re

from .capture_journal import load_capture
from .contracts import EVENT_FIELDS, canonical_json, normalize_time, utc_now, validate_event
from .outcomes import label_decision
from .source_inputs import label_captured_decision, snapshot_for_outcomes
from .source_review import build_source_review, validate_source_review


def _sha(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _time(value):
    if type(value) is not str:
        raise ValueError("审核时点必须是带时区的 ISO 字符串")
    return normalize_time(value)


def _review_event(store, event_id, receipt, as_of):
    if type(event_id) is not str or re.fullmatch(r"[0-9a-f]{64}", event_id) is None:
        raise ValueError("review_event_id 必须是小写 SHA256")
    event = store.get_event(event_id)
    fields = set(EVENT_FIELDS) | {"event_id", "payload_hash", "recorded_at"}
    if type(event) is not dict or set(event) != fields:
        raise ValueError("审核事件不存在或字段不符")
    body = {name: event[name] for name in EVENT_FIELDS}
    normalized = validate_event(body)
    if canonical_json(normalized) != canonical_json(body):
        raise ValueError("审核事件基础字段不是规范格式")
    if event["event_id"] != event_id or event_id != _sha(body) or event["payload_hash"] != _sha(body["payload"]):
        raise ValueError("审核事件内容哈希不匹配")
    if body["kind"] != "source.review" or body["source"] != "ai_operator.review_gate" or body["environment"] != "shadow":
        raise ValueError("所选事件不是来源审核事件")
    review = validate_source_review(body["payload"], receipt)
    compiled = _time(review["compiled_at"])
    recorded = _time(event["recorded_at"])
    if body["source_id"] != review["review_id"] or body["occurred_at"] != compiled or body["available_at"] != compiled:
        raise ValueError("审核事件身份或时间与报告不符")
    if event["recorded_at"] != recorded or recorded < compiled or recorded > utc_now():
        raise ValueError("审核落库时间非法或系统时钟倒退")
    durable = max(recorded, compiled)
    if durable > _time(as_of):
        raise ValueError("审核事件在查询时点尚未落库可用")
    return review, durable


def record_source_review(store, capture_event_id, reviewer, evidence) -> dict:
    """从库中绑定的真实receipt生成审核报告，追加独立事件。"""
    if store.readonly:
        raise ValueError("只读记忆库不允许登记来源审核")
    receipt = load_capture(store, capture_event_id, utc_now())
    review = build_source_review(receipt, reviewer, evidence)
    event_id = store.append_event({"schema_version": 1, "kind": "source.review", "environment": "shadow",
                                   "source": "ai_operator.review_gate", "source_id": review["review_id"],
                                   "occurred_at": review["compiled_at"], "available_at": review["compiled_at"],
                                   "payload": review})
    checked, durable = _review_event(store, event_id, receipt, utc_now())
    return {"review_event_id": event_id, "capture_event_id": capture_event_id,
            "review_id": checked["review_id"], "status": checked["status"],
            "recorded_available_at": durable, "eligible": False, "production_eligible": False}


def assess_review_gate(store, capture_event_id, review_event_id, as_of) -> dict:
    """按落库时点重验具体报告；匹配通过也只进入待人审状态。"""
    instant = _time(as_of)
    receipt = load_capture(store, capture_event_id, instant)
    review, durable = _review_event(store, review_event_id, receipt, instant)
    status = {"needs_evidence": "blocked_missing_evidence", "mismatch": "blocked_mismatch",
              "ready_for_human_review": "blocked_pending_human_review"}[review["status"]]
    return {"capture_event_id": capture_event_id, "review_event_id": review_event_id,
            "status": status, "checks": review["checks"], "recorded_available_at": durable,
            "eligible": False, "production_eligible": False}


def label_journal_decision(store, capture_event_id, pack, decision, evaluated_at, horizons=None) -> dict:
    """库内接收证明绑定标签；未审核来源不形成成熟训练目标。"""
    instant = _time(evaluated_at)
    receipt = load_capture(store, capture_event_id, instant)
    snapshot, calendar = receipt["snapshot"], receipt["calendar_capture"]
    label_captured_decision(snapshot, calendar, pack, decision, instant, horizons=horizons)
    adapted = snapshot_for_outcomes(snapshot, calendar)
    for bar in adapted["bars"]:
        bar["available_at"] = max(_time(bar["available_at"]), receipt["recorded_available_at"])
    adapted["source"]["source_hash"] = _sha({"capture_event_id": capture_event_id,
                                             "snapshot_id": snapshot["snapshot_id"],
                                             "calendar_capture_id": calendar["capture_id"],
                                             "recorded_available_at": receipt["recorded_available_at"]})
    return label_decision(pack, decision, adapted["bars"], adapted["calendar"],
                          adapted["source"], instant, horizons=horizons)
