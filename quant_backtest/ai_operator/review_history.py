"""按真实落库时点回放人工审核，持续阻断尚未认证的来源资格。"""

from . import contracts
from .capture_journal import load_capture
from .review_gate import assess_review_gate


MAX_CANDIDATES = 10000
_HUMAN_STATUS = {
    "pending": "blocked_pending_human_review",
    "unverified": "blocked_authentication_unconfigured",
    "rejected": "blocked_human_rejected",
    "revoked": "blocked_human_revoked",
    "expired": "blocked_human_expired",
    "conflict": "blocked_human_conflict",
    "approved": "blocked_provenance_gaps",
}


def _human_state(decisions, as_of, authenticated):
    if not decisions:
        return "pending", None
    if not authenticated:
        return "unverified", None
    times = [decision["recorded_available_at"] for decision in decisions]
    if len(set(times)) != len(times):
        return "conflict", None
    active = None
    revoked = set()
    for decision in decisions:
        statement = decision["statement"]
        if statement["action"] == "revoke":
            revoked.add(statement["target_decision_event_id"])
        else:
            active = decision
    if active is None:
        raise ValueError("可见撤销记录缺少其可见批准主决定")
    active_id = active["decision_event_id"]
    statement = active["statement"]
    if statement["action"] == "reject":
        return "rejected", active_id
    if active_id in revoked:
        return "revoked", active_id
    if statement["expires_at"] <= as_of:
        return "expired", active_id
    return "approved", active_id


def assess_human_review_history(store, capture_event_id, review_event_id, as_of, *, verifier=None) -> dict:
    """在一致只读快照中重验显式审核历史；本批所有来源资格仍为false。"""
    if type(as_of) is not str:
        raise ValueError("as_of 必须是带时区的 ISO 字符串")
    instant = contracts.normalize_time(as_of)
    now = contracts.normalize_time(contracts.utc_now())
    if instant > now:
        raise ValueError("历史审核查询不能使用未来 as_of")
    with store._transaction(write=False):
        gate = assess_review_gate(store, capture_event_id, review_event_id, instant)
        receipt = load_capture(store, capture_event_id, instant)
        rows = store._conn.execute(
            "SELECT event_id FROM events WHERE kind=? LIMIT ?",
            ("source.human_decision", MAX_CANDIDATES + 1)).fetchall()
        if len(rows) > MAX_CANDIDATES:
            raise ValueError("人工审核候选事件超过10000条，拒绝返回截断历史")
        decisions = []
        if rows:
            from .human_review import _decision_event, load_human_decision

            validation_now = contracts.normalize_time(contracts.utc_now())
            for row in rows:
                event_id = row["event_id"]
                # 以系统当前时点重验，合法的后到记录仅对历史查询不可见。
                _, statement, _, durable = _decision_event(store, event_id, validation_now)
                if statement["capture_event_id"] != capture_event_id or statement["review_event_id"] != review_event_id or durable > instant:
                    continue
                if verifier is None:
                    decision = {"decision_event_id": event_id, "statement": statement,
                                "recorded_available_at": durable}
                else:
                    decision = load_human_decision(store, event_id, validation_now, verifier=verifier)
                decisions.append(decision)
        decisions.sort(key=lambda decision: decision["recorded_available_at"])
        human_status, active_id = _human_state(decisions, instant, verifier is not None)
        status = gate["status"] if gate["status"] in ("blocked_missing_evidence", "blocked_mismatch") else _HUMAN_STATUS[human_status]
        gaps = ["真实下载链及来源真实性尚未认证", "历史到达与历史可用时间尚未认证"]
        if receipt["snapshot"]["price_basis"] in ("qfq_snapshot", "hfq_snapshot"):
            gaps.append("复权算法与因子尚未认证")
        return {"capture_event_id": capture_event_id, "review_event_id": review_event_id,
                "as_of": instant, "review_gate_status": gate["status"], "human_status": human_status,
                "status": status, "decision_event_ids": [decision["decision_event_id"] for decision in decisions],
                "active_decision_event_id": active_id, "checks": gate["checks"], "remaining_gaps": gaps,
                "source_verified": False, "historical_availability_verified": False,
                "training_eligible": False, "eligible": False, "production_eligible": False}
