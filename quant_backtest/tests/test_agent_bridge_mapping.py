"""Offline cross-domain translation and actual outbox receipt binding tests."""
from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone

import pytest

from quant_backtest.agent_bridge import (
    ACK_FIELD_MAP,
    REPORT_FIELD_MAP,
    OutboxBindingAdapter,
    to_agent_ack,
    to_sector_report,
)
from quant_backtest.quant_sector.upstream_inbox import UpstreamInbox, UpstreamInboxError
from quant_backtest.watch_dispatch import freeze_offline
from quant_backtest.watch_dispatch.agent_reporting import AgentIdentity, AgentReport
from quant_backtest.agent_bridge.upstream_ack_authority import UpstreamAckAuthority
from quant_backtest.watch_dispatch.route_outbox import (
    ACCEPTED_VERIFIED,
    ReportFingerprint,
    RouteOutbox,
)
from quant_backtest.watch_dispatch.signal_pool import (
    AcceptanceReceipt,
    SignalPool,
    SignalPoolDraft,
    SignalPoolError,
    STATUS_CLAIMED,
)
from quant_backtest.watch_dispatch.evidence_store import EvidenceStore

T = datetime(2026, 10, 19, 2, 0, tzinfo=timezone.utc)
END = T + timedelta(hours=1)
ROUTE = "QUANT_UPSTREAM_REPORT"


def envelope(event_id="event-1"):
    return freeze_offline(
        schema_version=1, evidence_version=1, previous_version_hash=None,
        pool="B", tenant_or_owner_id="owner1", market="SH", code="600000",
        source_membership_id="member1", source_revision="rev1", rule_id="r1",
        rule_version=1, comparator="GE", threshold="1.00", unit="ratio",
        window="5m", observed_value="1.10", baseline_value="1.00",
        raw_ref="raw:1", raw_digest="a" * 64, source_id="src1",
        calendar_id="cal1", source_as_of=T, visible_at=T, detected_at=T,
        freeze_at=T, valid_until=END, facts=("observed",),
        hypotheses=("hypothesis",), supporting_refs=("raw:1",),
        contradicting_refs=(), confirmation_condition="condition",
        invalidation_condition="invalid", event_id=event_id)


def identities():
    return (
        AgentIdentity("watch1", "WATCH", "registry:watch", T - timedelta(days=1), END),
        AgentIdentity("sector1", "QUANT_SECTOR", "registry:sector", T - timedelta(days=1), END),
    )


def report_for(env):
    sender, receiver = identities()
    return AgentReport("report1", "ANOMALY", env.event_id,
                       env.evidence_version, env.envelope_hash, sender, receiver, T)


def settled_chain():
    env = envelope()
    report = report_for(env)
    mapped = to_sector_report(report)
    inbox = UpstreamInbox()
    upstream_receipt = inbox.accept(mapped, now=T + timedelta(seconds=1))
    ack = to_agent_ack(upstream_receipt.ack)
    # P1 安全口径（848c95b0 / 955b4955）：active ACK 必须由构造注入的
    # UpstreamAckAuthority 对"原报告指纹 + inbox 当前活跃回执"做真实校验；
    # 裸 RouteOutbox 不再允许直接 settle 为 ACCEPTED_VERIFIED。
    outbox = RouteOutbox(active_ack_port=UpstreamAckAuthority(inbox))
    fingerprint = ReportFingerprint(
        report_id=report.report_id,
        report_kind=report.report_kind,
        event_id=report.event_id,
        evidence_version=report.evidence_version,
        envelope_hash=report.envelope_hash,
        sender_agent_id=report.sender.agent_id,
        receiver_agent_id=report.receiver.agent_id,
        created_at=report.created_at,
    )
    outbox.plan(env, route=ROUTE, target_type="AGENT", target_id=report.receiver.agent_id,
                policy_version="agent-report-v1", control_epoch=0,
                intent_id="report-report1", created_at=T, valid_until=END,
                report_fingerprint=fingerprint)
    attempt = outbox.claim(env, route=ROUTE, target_type="AGENT",
                           target_id=report.receiver.agent_id, claim_token="token",
                           claim_at=T + timedelta(seconds=2), lease_until=END)
    settled = outbox.finish(attempt.attempt_id, outcome=ACCEPTED_VERIFIED,
                            external_message_id=ack.ack_id,
                            verification_source=ack.verification_source,
                            observed_at=ack.acked_at,
                            now=T + timedelta(seconds=4))
    assert settled.receipt is not None
    store = EvidenceStore()
    store.freeze(env)
    pool = SignalPool(store)
    pool.append(SignalPoolDraft("signal1", "ANOMALY", env.event_id,
                                env.envelope_hash, env.evidence_version),
                producer=report.sender, store=store, now=T)
    pool.claim("signal1", lease_until=END, now=T)
    pool_receipt = AcceptanceReceipt(ROUTE, attempt.attempt_id, ack.ack_id)
    return env, report, mapped, inbox, outbox, pool, pool_receipt


def test_field_maps_cover_all_payload_fields_without_mutation_b10():
    env, report, mapped, inbox, outbox, pool, pool_receipt = settled_chain()
    assert set(REPORT_FIELD_MAP.values()) == {
        item.name for item in fields(mapped) if item.name != "non_executable"}
    assert REPORT_FIELD_MAP["envelope_hash"] == "evidence_hash"
    assert REPORT_FIELD_MAP["sender.agent_id"] == "sender_agent_id"
    assert REPORT_FIELD_MAP["receiver.agent_id"] == "receiver_agent_id"
    for source, target in REPORT_FIELD_MAP.items():
        root, _, nested = source.partition(".")
        assert getattr(mapped, target) == (getattr(getattr(report, root), nested)
                                           if nested else getattr(report, root))
    assert mapped.evidence_hash == report.envelope_hash
    assert mapped.envelope_hash != report.envelope_hash
    assert to_sector_report(report) == mapped
    assert set(ACK_FIELD_MAP.values()) == {
        item.name for item in fields(inbox.receipt_for_report(report.report_id).ack)}
    original_ack = inbox.receipt_for_report(report.report_id).ack
    watch_ack = to_agent_ack(original_ack)
    for source, target in ACK_FIELD_MAP.items():
        assert getattr(watch_ack, target) == getattr(original_ack, source)
    assert watch_ack.acked_at is original_ack.acked_at
    assert to_agent_ack(original_ack) == watch_ack
    assert inbox.receipt_for_report(report.report_id).ack is original_ack


def test_changed_mapped_field_drifts_inbox_key_b11():
    env, report, mapped, inbox, outbox, pool, pool_receipt = settled_chain()
    altered = replace(mapped, created_at=T + timedelta(microseconds=1))
    assert altered.envelope_hash != mapped.envelope_hash
    with pytest.raises(UpstreamInboxError, match="RECEIPT_CONFLICT"):
        inbox.accept(altered, now=T + timedelta(seconds=5))


def test_adapter_real_chain_and_no_mutation_b8():
    env, report, mapped, inbox, outbox, pool, pool_receipt = settled_chain()
    adapter = OutboxBindingAdapter(outbox, env)
    attempts_before = dict(outbox._attempts)
    entries_before = dict(pool._entries)
    binding = adapter.binding_for(pool_receipt.attempt_id)
    assert (binding.attempt_id, binding.external_message_id, binding.outcome,
            binding.route, binding.event_id, binding.evidence_hash,
            binding.evidence_version) == (
                pool_receipt.attempt_id, pool_receipt.external_ref,
                ACCEPTED_VERIFIED, ROUTE, env.event_id, env.envelope_hash,
                env.evidence_version)
    assert outbox._attempts == attempts_before
    assert pool._entries == entries_before
    assert pool.mark_accepted("signal1", receipt=pool_receipt,
                              binding_port=adapter).status == "ACCEPTED"


def test_adapter_unknown_attempt_and_no_mutation_b7():
    env, report, mapped, inbox, outbox, pool, pool_receipt = settled_chain()
    adapter = OutboxBindingAdapter(outbox, env)
    assert adapter.binding_for("forged") is None
    with pytest.raises(SignalPoolError, match="RECEIPT_ATTEMPT_UNKNOWN"):
        pool.mark_accepted("signal1", receipt=replace(pool_receipt, attempt_id="forged"),
                           binding_port=adapter)
    assert pool.get("signal1").status == STATUS_CLAIMED
    assert pool._leases["signal1"] == END


def test_adapter_rejects_wrong_external_ref_and_route():
    env, report, mapped, inbox, outbox, pool, pool_receipt = settled_chain()
    adapter = OutboxBindingAdapter(outbox, env)
    for changed, code in (
        ({"external_ref": "forged"}, "RECEIPT_REF_MISMATCH"),
        ({"route": "FEISHU"}, "RECEIPT_ROUTE_MISMATCH"),
    ):
        with pytest.raises(SignalPoolError, match=code):
            pool.mark_accepted("signal1", receipt=replace(pool_receipt, **changed),
                               binding_port=adapter)
    assert pool.get("signal1").status == STATUS_CLAIMED


def test_adapter_rejects_missing_private_attempt_and_intent_index():
    env, report, mapped, inbox, outbox, pool, pool_receipt = settled_chain()
    adapter = OutboxBindingAdapter(outbox, env)
    attempt = outbox._attempts.pop(pool_receipt.attempt_id)
    assert adapter.binding_for(pool_receipt.attempt_id) is None
    outbox._attempts[pool_receipt.attempt_id] = attempt
    intent = outbox._intents.pop(attempt.idempotency_key)
    assert adapter.binding_for(pool_receipt.attempt_id) is None
    outbox._intents[attempt.idempotency_key] = intent
    assert adapter.binding_for(pool_receipt.attempt_id) is not None


def test_adapter_rejects_mismatched_receipt_or_stale_outbox():
    env, report, mapped, inbox, outbox, pool, pool_receipt = settled_chain()
    adapter = OutboxBindingAdapter(outbox, env)
    attempt = outbox._attempts[pool_receipt.attempt_id]
    for bad_receipt in (
        replace(attempt.receipt, attempt_id="unrelated"),
        replace(attempt.receipt, external_message_id=""),
        replace(attempt.receipt, verification_source=""),
    ):
        outbox._attempts[pool_receipt.attempt_id] = replace(attempt, receipt=bad_receipt)
        assert adapter.binding_for(pool_receipt.attempt_id) is None
    outbox._attempts[pool_receipt.attempt_id] = attempt
    outbox.revoke_envelope(env, epoch=1, now=T + timedelta(seconds=5))
    assert adapter.binding_for(pool_receipt.attempt_id) is None


def test_adapter_cross_signal_real_receipt_still_rejected():
    env, report, mapped, inbox, outbox, pool, pool_receipt = settled_chain()
    other = envelope("event-other")
    store = EvidenceStore()
    store.freeze(other)
    other_pool = SignalPool(store)
    other_pool.append(SignalPoolDraft("signal-other", "ANOMALY", other.event_id,
                                      other.envelope_hash, other.evidence_version),
                      producer=report.sender, store=store, now=T)
    other_pool.claim("signal-other", lease_until=END, now=T)
    with pytest.raises(SignalPoolError, match="RECEIPT_ATTEMPT_UNKNOWN"):
        other_pool.mark_accepted("signal-other", receipt=pool_receipt,
                                 binding_port=OutboxBindingAdapter(outbox, other))
    assert other_pool.get("signal-other").status == STATUS_CLAIMED
