"""Offline public-entry counterexamples; none grants production authority.

A locally constructed, shape-valid report is not independently sourced. A
same-generation outbox receipt is not an independently verified upstream ACK.
Even when both ledgers appear to agree, the controlled signal pool must not
promote their caller-supplied evidence into an ACCEPTED signal.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from quant_backtest.agent_bridge.trusted_acceptance import TrustedAcceptanceBindingAdapter
from quant_backtest.quant_sector.upstream_inbox import QuantUpstreamReport, UpstreamInbox
from quant_backtest.watch_dispatch import freeze_offline
from quant_backtest.watch_dispatch.agent_reporting import AgentIdentity, AgentReport
from quant_backtest.watch_dispatch.evidence_store import EvidenceStore
from quant_backtest.watch_dispatch.route_outbox import (
    ACCEPTED_VERIFIED,
    ReportFingerprint,
    RouteOutbox,
)
from quant_backtest.watch_dispatch.signal_pool import (
    CONTROLLED_AUTHORITY_UNAVAILABLE,
    CONTROLLED_PENDING_ROOT,
    AcceptanceReceipt,
    ControlledSignalScope,
    SignalPool,
    SignalPoolDraft,
    SignalPoolError,
)

START = datetime(2026, 10, 19, 2, 0, tzinfo=timezone.utc)
END = START + timedelta(hours=1)
ROUTE = "QUANT_UPSTREAM_REPORT"


def _at(seconds: int) -> datetime:
    """Return a deterministic UTC timestamp inside the offline lease."""
    return START + timedelta(seconds=seconds)


def _envelope():
    """Freeze-shaped evidence with no external source credential."""
    return freeze_offline(
        schema_version=1, evidence_version=1, previous_version_hash=None,
        pool="B", tenant_or_owner_id="owner1", market="SH", code="600000",
        source_membership_id="member1", source_revision="rev1", rule_id="r1",
        rule_version=1, comparator="GE", threshold="1.00", unit="ratio",
        window="5m", observed_value="1.10", baseline_value="1.00",
        raw_ref="raw:1", raw_digest="a" * 64, source_id="src1",
        calendar_id="cal1", source_as_of=START, visible_at=START,
        detected_at=START, freeze_at=START, valid_until=END,
        facts=("observed",), hypotheses=("hypothesis",),
        supporting_refs=("raw:1",), contradicting_refs=(),
        confirmation_condition="condition", invalidation_condition="invalid",
        event_id="event1")


def _manual_inbox_report(envelope) -> QuantUpstreamReport:
    """Construct only valid public fields, without claiming source authority."""
    return QuantUpstreamReport(
        report_id="locally-made-report", report_kind="ANOMALY",
        event_id=envelope.event_id, evidence_hash=envelope.envelope_hash,
        evidence_version=envelope.evidence_version,
        sender_agent_id="watch1", receiver_agent_id="sector1",
        created_at=START)


def test_public_inbox_accepts_local_shape_valid_report_and_issues_local_ack() -> None:
    """The inbox's ACK is genuine locally, not proof of upstream provenance."""
    report = _manual_inbox_report(_envelope())
    inbox = UpstreamInbox()
    accepted = inbox.accept(report, now=_at(2))
    assert inbox.is_active(accepted.receipt_id)
    assert inbox.receipt_for_report(report.report_id) == accepted
    assert accepted.envelope_hash == report.envelope_hash
    assert accepted.ack.report_id == report.report_id
    assert accepted.ack.receiver_agent_id == report.receiver_agent_id
    assert accepted.ack.acked_at == accepted.accepted_at == _at(2)
    assert accepted.ack.verification_source == "upstream-inbox-v1"
    assert inbox.accept(report, now=_at(3)) == accepted


@pytest.mark.parametrize("use_local_inbox_ack", [False, True])
def test_same_generation_public_finish_needs_no_active_ack_port(
        use_local_inbox_ack: bool) -> None:
    """A caller-supplied ACK can settle an offline outbox without a live port."""
    envelope = _envelope()
    report = _manual_inbox_report(envelope)
    inbox = UpstreamInbox()
    local_ack = inbox.accept(report, now=_at(2)).ack
    outbox = RouteOutbox()  # Explicitly no independently bound ActiveAckPort.
    fingerprint = ReportFingerprint(
        report_id=report.report_id, report_kind=report.report_kind,
        event_id=report.event_id, evidence_version=report.evidence_version,
        envelope_hash=report.evidence_hash, sender_agent_id=report.sender_agent_id,
        receiver_agent_id=report.receiver_agent_id, created_at=report.created_at)
    outbox.plan(
        envelope, route=ROUTE, target_type="AGENT", target_id="sector1",
        policy_version="agent-report-v1", control_epoch=0,
        intent_id="local-intent", created_at=START, valid_until=END,
        report_fingerprint=fingerprint)
    attempt = outbox.claim(
        envelope, route=ROUTE, target_type="AGENT", target_id="sector1",
        claim_token="local-claim", claim_at=_at(1), lease_until=_at(20))
    ack_id = local_ack.ack_id if use_local_inbox_ack else "caller-supplied-ack"
    source = (local_ack.verification_source if use_local_inbox_ack
              else "caller-supplied-source")
    observed_at = local_ack.acked_at if use_local_inbox_ack else _at(2)
    settled = outbox.finish(
        attempt.attempt_id, outcome=ACCEPTED_VERIFIED, now=_at(3),
        external_message_id=ack_id, verification_source=source,
        observed_at=observed_at)
    assert settled.outcome == ACCEPTED_VERIFIED
    assert settled.receipt is not None
    assert settled.receipt.external_message_id == ack_id
    assert settled.receipt.verification_source == source
    assert settled.receipt.observed_at == observed_at
    assert outbox.production_permitted is False
    assert outbox.notification_permitted is False
    assert outbox.trading_permitted is False
    assert settled.production_permitted is False
    assert settled.notification_permitted is False
    assert settled.trading_permitted is False


def test_controlled_pool_refuses_even_plausible_local_ack_and_fixed_binding() -> None:
    """A read-only binding cannot promote a manufactured report to authority."""
    envelope = _envelope()
    store = EvidenceStore()
    store.freeze(envelope)
    sender = AgentIdentity(
        "watch1", "WATCH", "registry:watch", START - timedelta(days=1), END)
    receiver = AgentIdentity(
        "sector1", "QUANT_SECTOR", "registry:sector",
        START - timedelta(days=1), END)
    agent_report = AgentReport(
        "locally-made-report", "ANOMALY", envelope.event_id,
        envelope.evidence_version, envelope.envelope_hash, sender, receiver, START)
    inbox = UpstreamInbox()
    local_ack = inbox.accept(_manual_inbox_report(envelope), now=_at(2)).ack
    outbox = RouteOutbox()
    outbox.plan(
        envelope, route=ROUTE, target_type="AGENT", target_id=receiver.agent_id,
        policy_version="agent-report-v1", control_epoch=0,
        intent_id="local-intent", created_at=START, valid_until=END,
        report_fingerprint=ReportFingerprint(
            agent_report.report_id, agent_report.report_kind,
            agent_report.event_id, agent_report.evidence_version,
            agent_report.envelope_hash, sender.agent_id, receiver.agent_id,
            agent_report.created_at))
    attempt = outbox.claim(
        envelope, route=ROUTE, target_type="AGENT", target_id=receiver.agent_id,
        claim_token="local-claim", claim_at=_at(1), lease_until=_at(20))
    settled = outbox.finish(
        attempt.attempt_id, outcome=ACCEPTED_VERIFIED, now=_at(3),
        external_message_id=local_ack.ack_id,
        verification_source=local_ack.verification_source,
        observed_at=local_ack.acked_at)
    assert settled.outcome == ACCEPTED_VERIFIED
    fixed = TrustedAcceptanceBindingAdapter(
        outbox, envelope, agent_report, inbox, now=_at(4))
    assert fixed.binding_for(attempt.attempt_id) is not None
    draft = SignalPoolDraft(
        "signal1", "ANOMALY", envelope.event_id,
        envelope.envelope_hash, envelope.evidence_version)
    scope = ControlledSignalScope(
        draft.signal_id, sender.agent_id, draft.report_kind,
        draft.event_id, draft.evidence_hash, draft.evidence_version)
    pool = SignalPool(store, controlled_scope=scope, acceptance_binding=fixed)
    pool.append(draft, producer=sender, store=store, now=START)
    claimed = pool.claim(draft.signal_id, lease_until=_at(20), now=_at(1))
    acceptance = AcceptanceReceipt(ROUTE, attempt.attempt_id, local_ack.ack_id)
    with pytest.raises(SignalPoolError,
                       match=f"^{CONTROLLED_AUTHORITY_UNAVAILABLE}$"):
        pool.mark_accepted(
            draft.signal_id, receipt=acceptance, binding_port=fixed, now=_at(4))
    assert pool.get(draft.signal_id, now=_at(4)) == claimed
    assert pool.receipt(draft.signal_id) is None
    assert pool.acceptance_mode == CONTROLLED_PENDING_ROOT
    for component in (store, outbox, settled, fixed, pool, claimed):
        assert component.production_permitted is False
        assert component.notification_permitted is False
        assert component.trading_permitted is False
