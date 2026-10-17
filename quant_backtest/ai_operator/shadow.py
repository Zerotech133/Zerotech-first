"""严格时点事实包与仅供审计的影子建议。"""

import hashlib
import json

from .contracts import canonical_json, normalize_time


_FACT_FIELDS = frozenset({"id", "source", "occurred_at", "available_at", "data"})
_VERSION_FIELDS = frozenset({"data", "algorithm", "knowledge", "rules"})
_PACK_FIELDS = frozenset({"schema_version", "environment", "code", "period", "as_of", "facts", "versions", "fact_hash"})
_DECISION_FIELDS = frozenset({
    "schema_version", "environment", "code", "period", "as_of", "fact_hash", "versions",
    "action", "evidence_ids", "rationale", "valid_until", "confidence", "execution_allowed", "decision_id",
})
_DECISION_TRACE_FIELDS = frozenset({"trace_id", "idempotency_key"})
# 仅拦截已知后验字段；任意 data 仍需来源与可用时点审核。
_POSTERIOR_FIELDS = frozenset({
    "post", "forward_return", "label", "outcome",
    "r5", "r10", "r20", "r60", "ret5", "ret10", "ret20", "ret60",
    "mature5", "mature20", "post10", "post_hm10",
})
_ACTIONS = frozenset({"observe", "candidate_buy", "candidate_sell", "abstain"})


def _freeze(value):
    return json.loads(canonical_json(value))


def _hash(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _nonempty(value, name):
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} 必须是非空字符串")
    return value


def _fields(value, fields, name):
    if type(value) is not dict or set(value) != fields:
        raise ValueError(f"{name} 必须是字段完整且无未知字段的 JSON 对象")


def _time(value, name):
    if type(value) is not str:
        raise ValueError(f"{name} 必须是带时区的 ISO 字符串")
    return normalize_time(value)


def _check_posterior(value):
    if type(value) is dict:
        forbidden = set(value) & _POSTERIOR_FIELDS
        if forbidden:
            raise ValueError("事实包不允许包含已知后验字段：" + "、".join(sorted(forbidden)))
        for child in value.values():
            _check_posterior(child)
    elif type(value) is list:
        for child in value:
            _check_posterior(child)


def build_fact_pack(code, period, as_of, facts: list[dict], versions: dict) -> dict:
    _nonempty(code, "code")
    _nonempty(period, "period")
    instant = _time(as_of, "as_of")
    if type(facts) is not list:
        raise ValueError("facts 必须是 JSON 列表")
    _fields(versions, _VERSION_FIELDS, "versions")
    for key, value in versions.items():
        _nonempty(value, f"versions.{key}")
    items = _freeze(facts)
    ids = set()
    for item in items:
        _fields(item, _FACT_FIELDS, "事实")
        fact_id = _nonempty(item["id"], "事实 id")
        if fact_id in ids:
            raise ValueError("事实 id 必须唯一")
        ids.add(fact_id)
        _nonempty(item["source"], "事实 source")
        if type(item["data"]) is not dict:
            raise ValueError("事实 data 必须是 JSON 对象")
        occurred = _time(item["occurred_at"], "occurred_at")
        available = _time(item["available_at"], "available_at")
        if not occurred <= available <= instant:
            raise ValueError("事实时间必须满足 occurred_at <= available_at <= as_of，拒绝未来事实")
        item["occurred_at"] = occurred
        item["available_at"] = available
    result = {
        "schema_version": 1, "environment": "shadow", "code": code, "period": period,
        "as_of": instant, "facts": items, "versions": _freeze(versions),
    }
    _check_posterior(result)
    result["fact_hash"] = _hash(result)
    return result


def _validate_pack(pack):
    _fields(pack, _PACK_FIELDS, "事实包")
    frozen = _freeze(pack)
    if type(frozen["schema_version"]) is not int or frozen["schema_version"] != 1:
        raise ValueError("事实包仅支持 schema_version=1")
    if frozen["environment"] != "shadow":
        raise ValueError("事实包 environment 必须为 shadow")
    # 重建用于完整语义验证；哈希仍覆盖收到的完整 JSON 时间表示。
    build_fact_pack(frozen["code"], frozen["period"], frozen["as_of"], frozen["facts"], frozen["versions"])
    expected = _hash({key: value for key, value in frozen.items() if key != "fact_hash"})
    if type(frozen["fact_hash"]) is not str or frozen["fact_hash"] != expected:
        raise ValueError("事实包 fact_hash 不匹配，内容可能被篡改")
    return frozen


def make_shadow_decision(pack: dict, action: str, evidence_ids: list[str], rationale: str,
                         valid_until: str, trace_id=None, idempotency_key=None) -> dict:
    frozen = _validate_pack(pack)
    if type(action) is not str or action not in _ACTIONS:
        raise ValueError("action 仅支持 observe、candidate_buy、candidate_sell 或 abstain")
    if type(evidence_ids) is not list:
        raise ValueError("evidence_ids 必须是 JSON 列表")
    for evidence_id in evidence_ids:
        _nonempty(evidence_id, "evidence_id")
    if len(set(evidence_ids)) != len(evidence_ids):
        raise ValueError("evidence_ids 必须唯一")
    fact_ids = {item["id"] for item in frozen["facts"]}
    if not set(evidence_ids).issubset(fact_ids):
        raise ValueError("证据必须引用事实包内的事实 id")
    if action in {"candidate_buy", "candidate_sell"} and not evidence_ids:
        raise ValueError("候选买卖必须提供至少一项证据")
    _nonempty(rationale, "rationale")
    expiry = _time(valid_until, "valid_until")
    if expiry <= _time(frozen["as_of"], "as_of"):
        raise ValueError("valid_until 必须晚于 as_of")
    result = {key: frozen[key] for key in ("schema_version", "environment", "code", "period", "as_of", "fact_hash", "versions")}
    result.update(action=action, evidence_ids=_freeze(evidence_ids), rationale=rationale,
                  valid_until=expiry, confidence=None, execution_allowed=False)
    result["decision_id"] = _hash(result)
    if trace_id is not None:
        _nonempty(trace_id, "trace_id")
        result["trace_id"] = trace_id
    if idempotency_key is not None:
        _nonempty(idempotency_key, "idempotency_key")
        result["idempotency_key"] = idempotency_key
    return result


def record_shadow_decision(store, pack, decision) -> dict:
    frozen_pack = _validate_pack(pack)
    if type(decision) is not dict or set(decision) - (_DECISION_FIELDS | _DECISION_TRACE_FIELDS):
        raise ValueError("影子决策包含未知字段")
    if not _DECISION_FIELDS.issubset(decision):
        raise ValueError("影子决策必须是字段完整的 JSON 对象")
    frozen_decision = _freeze(decision)
    rebuilt = make_shadow_decision(frozen_pack, frozen_decision["action"], frozen_decision["evidence_ids"],
                                   frozen_decision["rationale"], frozen_decision["valid_until"],
                                   frozen_decision.get("trace_id"),
                                   frozen_decision.get("idempotency_key"))
    if canonical_json(frozen_decision) != canonical_json(rebuilt):
        raise ValueError("影子决策与事实包重建结果不符，内容或 decision_id 可能被篡改")
    provenance = {"schema_version": 1, "environment": "shadow", "source": "ai_operator.shadow",
                  "occurred_at": frozen_pack["as_of"], "available_at": frozen_pack["as_of"]}
    trace_id = frozen_decision.get("trace_id")
    idempotency_key = frozen_decision.get("idempotency_key")
    # append_event 各自提交；第二次失败后重试会复用已落库的事实事件。
    fact_payload = dict(frozen_pack)
    decision_payload = dict(rebuilt, fact_event_id=None)
    if trace_id is not None:
        fact_payload["trace_id"] = trace_id
        decision_payload["trace_id"] = trace_id
    if idempotency_key is not None:
        decision_payload["idempotency_key"] = idempotency_key
    fact_event_id = store.append_event(dict(provenance, kind="shadow.fact_pack",
                                           source_id=frozen_pack["fact_hash"],
                                           payload=fact_payload))
    decision_payload["fact_event_id"] = fact_event_id
    decision_event_id = store.append_event(dict(provenance, kind="shadow.decision",
                                               source_id=rebuilt["decision_id"],
                                               payload=decision_payload))
    return {"fact_event_id": fact_event_id, "decision_event_id": decision_event_id}
