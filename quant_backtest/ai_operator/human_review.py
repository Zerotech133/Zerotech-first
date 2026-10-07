"""由可信应用代码注入认证适配器的人工审核；默认拒绝认证。"""

import hashlib
import json
import re
import uuid

from .capture_journal import load_capture
from .contracts import EVENT_FIELDS, canonical_json, normalize_time, utc_now, validate_event
from .review_gate import _review_event, assess_review_gate


_STATEMENT_FIELDS = frozenset({"schema_version", "request_id", "action", "capture_event_id",
                              "review_event_id", "target_decision_event_id", "reason", "issued_at", "expires_at"})
_PRINCIPAL_FIELDS = frozenset({"principal_id", "authority_id", "policy_id", "role"})
_PAYLOAD_FIELDS = frozenset({"schema_version", "observation_id", "statement", "proof", "principal", "received_at"})


def _json_object(value, name, limit=None):
    if type(value) is not dict:
        raise ValueError(f"{name} 必须是严格 JSON 对象")
    try:
        text = canonical_json(value)
        encoded = text.encode("utf-8")
        if limit is not None and len(encoded) > limit:
            raise ValueError(f"{name} 规范 UTF8 超过 64KiB")
        return json.loads(text)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError(f"{name} 不是有效 UTF8 JSON") from exc


def _sha(value):
    try:
        return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    except UnicodeError as exc:
        raise ValueError("审核内容不是有效 UTF8 JSON") from exc


def _time(value):
    if type(value) is not str:
        raise ValueError("人工审核时点必须是带时区的 ISO 字符串")
    return normalize_time(value)


def _hex(value, size, name):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{" + str(size) + "}", value) is None:
        raise ValueError(f"{name} 必须是 {size} 位小写 hex")
    return value


def _statement(value):
    result = _json_object(value, "statement")
    if set(result) != _STATEMENT_FIELDS or type(result["schema_version"]) is not int or result["schema_version"] != 1:
        raise ValueError("statement 字段不符或版本不受支持")
    _hex(result["request_id"], 32, "request_id")
    for name in ("capture_event_id", "review_event_id"):
        _hex(result[name], 64, name)
    if type(result["action"]) is not str or result["action"] not in ("approve", "reject", "revoke"):
        raise ValueError("action 必须是 approve、reject 或 revoke")
    if type(result["reason"]) is not str or not result["reason"].strip():
        raise ValueError("reason 必须是非空字符串")
    if result["action"] == "revoke":
        _hex(result["target_decision_event_id"], 64, "target_decision_event_id")
    elif result["target_decision_event_id"] is not None:
        raise ValueError("仅 revoke 可以指定目标审核事件")
    result["issued_at"] = _time(result["issued_at"])
    if result["action"] == "approve":
        result["expires_at"] = _time(result["expires_at"])
    elif result["expires_at"] is not None:
        raise ValueError("reject 和 revoke 的 expires_at 必须为 null")
    return result


def _principal(value):
    result = _json_object(value, "principal")
    if set(result) != _PRINCIPAL_FIELDS:
        raise ValueError("认证主体字段不符")
    for name in ("principal_id", "authority_id", "policy_id"):
        if type(result[name]) is not str or not result[name].strip():
            raise ValueError("认证主体和授权策略标识必须是非空字符串")
    if type(result["role"]) is not str or result["role"] not in ("source_reviewer", "source_admin"):
        raise ValueError("认证主体没有来源审核权限")
    return result


def _adapter(verifier):
    if verifier is None or not callable(getattr(verifier, "verify", None)):
        raise ValueError("未配置可信应用代码认证适配器")
    return verifier


def _verify(verifier, statement, proof, received_at):
    statement_copy = _json_object(statement, "statement")
    proof_copy = _json_object(proof, "proof", 65536)
    received = _time(received_at)
    try:
        value = _adapter(verifier).verify(statement_copy, proof_copy, received_at=received)
    except Exception:
        # 适配器异常可能含认证秘密，仅输出固定错误，不保留异常链。
        raise ValueError("人工审核认证失败") from None
    if statement_copy != statement or proof_copy != proof:
        raise ValueError("认证适配器不得修改审核声明或证明")
    return _principal(value)


def _binding(store, statement, received):
    receipt = load_capture(store, statement["capture_event_id"], received)
    _, durable = _review_event(store, statement["review_event_id"], receipt, received)
    if statement["issued_at"] < durable or statement["issued_at"] > received:
        raise ValueError("声明签发时间早于审核落库或晚于系统接收")
    if statement["action"] == "approve":
        if statement["expires_at"] <= received:
            raise ValueError("approve 必须在系统接收时有未来 expires_at")
        gate = assess_review_gate(store, statement["capture_event_id"], statement["review_event_id"], received)
        if gate["status"] != "blocked_pending_human_review":
            raise ValueError("approve 必须绑定文档全部匹配的审核")


def _revoke_binding(statement, principal, target_statement, target_principal):
    if target_statement["action"] != "approve":
        raise ValueError("revoke 目标必须是真实 approve 事件")
    for name in ("capture_event_id", "review_event_id"):
        if statement[name] != target_statement[name]:
            raise ValueError("revoke 目标必须绑定同一 capture/review")
    identity = (principal["principal_id"], principal["authority_id"])
    original = (target_principal["principal_id"], target_principal["authority_id"])
    if principal["role"] != "source_admin" and identity != original:
        raise ValueError("仅原批准主体或 source_admin 可以撤销")


def _decision_event(store, decision_event_id, as_of, *, _approve_only=False):
    """供历史查询复用结构重验；此函数不提供身份认证结果。"""
    instant = _time(as_of)
    now = _time(utc_now())
    if instant > now:
        raise ValueError("人工审核查询时点不得在未来")
    _hex(decision_event_id, 64, "decision_event_id")
    event = store.get_event(decision_event_id)
    if type(event) is not dict or set(event) != set(EVENT_FIELDS) | {"event_id", "payload_hash", "recorded_at"}:
        raise ValueError("人工审核事件不存在或字段不符")
    body = {name: event[name] for name in EVENT_FIELDS}
    normalized = validate_event(body)
    if canonical_json(normalized) != canonical_json(body):
        raise ValueError("人工审核事件基础字段不是规范格式")
    if event["event_id"] != decision_event_id or decision_event_id != _sha(body) or event["payload_hash"] != _sha(body["payload"]):
        raise ValueError("人工审核事件内容哈希不匹配")
    if body["kind"] != "source.human_decision" or body["source"] != "ai_operator.human_review" or body["environment"] != "shadow":
        raise ValueError("所选事件不是人工审核事件")
    payload = _json_object(body["payload"], "payload")
    if set(payload) != _PAYLOAD_FIELDS or type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise ValueError("人工审核 payload 字段不符")
    observation = _hex(payload["observation_id"], 32, "observation_id")
    if uuid.UUID(hex=observation).version != 4:
        raise ValueError("observation_id 必须是系统 UUID4 观察标识")
    statement = _statement(payload["statement"])
    if statement != payload["statement"]:
        raise ValueError("保存的 statement 不是规范声明")
    if _approve_only and statement["action"] != "approve":
        raise ValueError("revoke 目标必须是真实 approve 事件")
    _json_object(payload["proof"], "proof", 65536)
    principal = _principal(payload["principal"])
    received, recorded = _time(payload["received_at"]), _time(event["recorded_at"])
    if payload["received_at"] != received or event["recorded_at"] != recorded:
        raise ValueError("人工审核接收或落库时间不是规范格式")
    if recorded < received or received > now or recorded > now:
        raise ValueError("人工审核落库时间非法或系统时钟倒退")
    if body["source_id"] != _sha(statement) or body["occurred_at"] != received or body["available_at"] != received:
        raise ValueError("人工审核事件身份或时间与声明不符")
    durable = max(recorded, received)
    if durable > instant:
        raise ValueError("人工审核事件在查询时点尚未落库可用")
    _binding(store, statement, received)
    if statement["action"] == "revoke":
        _, target_statement, target_principal, _ = _decision_event(
            store, statement["target_decision_event_id"], received, _approve_only=True)
        _revoke_binding(statement, principal, target_statement, target_principal)
    return event, statement, principal, durable


def _result(event, statement, principal, durable):
    return {"decision_event_id": event["event_id"], "statement": statement, "principal": principal,
            "recorded_available_at": durable, "event": event, "eligible": False, "production_eligible": False}


def load_human_decision(store, decision_event_id, as_of, *, verifier=None) -> dict:
    """重验保存声明的认证和实际可用时点；批准过期由历史资格层判断。"""
    _adapter(verifier)
    event, statement, principal, durable = _decision_event(store, decision_event_id, as_of)
    checked = _verify(verifier, statement, event["payload"]["proof"], event["payload"]["received_at"])
    if checked != principal:
        raise ValueError("重新认证主体与保存 principal 不一致")
    if statement["action"] == "revoke":
        target = load_human_decision(store, statement["target_decision_event_id"],
                                     event["payload"]["received_at"], verifier=verifier)
        _revoke_binding(statement, checked, target["statement"], target["principal"])
    return _result(event, statement, checked, durable)


def record_human_decision(store, statement, proof, *, verifier=None) -> dict:
    """认证后原子追加独立系统观察；重复声明仍产生新观察，不保证幂等。"""
    _adapter(verifier)
    if store.readonly:
        raise ValueError("只读记忆库不允许登记人工审核")
    normalized = _statement(statement)
    evidence = _json_object(proof, "proof", 65536)
    received = _time(utc_now())
    with store._transaction():
        _binding(store, normalized, received)
        principal = _verify(verifier, normalized, evidence, received)
        if normalized["action"] == "revoke":
            target = load_human_decision(store, normalized["target_decision_event_id"], received, verifier=verifier)
            _revoke_binding(normalized, principal, target["statement"], target["principal"])
        payload = {"schema_version": 1, "observation_id": uuid.uuid4().hex, "statement": normalized,
                   "proof": evidence, "principal": principal, "received_at": received}
        event_id, _ = store._insert_event({"schema_version": 1, "kind": "source.human_decision",
                                          "source": "ai_operator.human_review", "environment": "shadow",
                                          "source_id": _sha(normalized), "occurred_at": received,
                                          "available_at": received, "payload": payload})
        result = load_human_decision(store, event_id, utc_now(), verifier=verifier)
    return result
