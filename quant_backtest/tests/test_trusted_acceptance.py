"""Adversarial offline verification of exact outbox/inbox acceptance binding."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from quant_backtest.agent_bridge.mapping import to_sector_report
from quant_backtest.agent_bridge.trusted_acceptance import TrustedAcceptanceBindingAdapter
from quant_backtest.quant_sector.upstream_inbox import UpstreamInbox
from quant_backtest.watch_dispatch import freeze_offline
from quant_backtest.watch_dispatch.agent_reporting import AgentIdentity, AgentReport
from quant_backtest.watch_dispatch.evidence_store import EvidenceStore
from quant_backtest.watch_dispatch.route_outbox import (
    ACCEPTED_VERIFIED, ReportFingerprint, RouteOutbox,
)
from quant_backtest.watch_dispatch.signal_pool import (
    AcceptanceReceipt, SignalPool, SignalPoolDraft, SignalPoolError,
)

START = datetime(2026, 10, 19, 2, 0, tzinfo=timezone.utc)
END = START + timedelta(hours=1)
ROUTE = "QUANT_UPSTREAM_REPORT"


def _chain(*, accept: bool = True, fingerprint: bool = True,
           control_epoch: int = 0, inbox_epoch: int = 0,
           accepted_seconds: int = 3, observed_seconds: int = 3):
    """Build distinct in-memory outbox and authoritative inbox ledgers."""
    envelope = freeze_offline(
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
    sender = AgentIdentity("watch1", "WATCH", "registry:watch",
                           START - timedelta(days=1), END)
    receiver = AgentIdentity("sector1", "QUANT_SECTOR", "registry:sector",
                             START - timedelta(days=1), END)
    report = AgentReport("report1", "ANOMALY", envelope.event_id,
                         envelope.evidence_version, envelope.envelope_hash,
                         sender, receiver, START)
    outbox = RouteOutbox()
    report_fingerprint = ReportFingerprint(
        report.report_id, report.report_kind, report.event_id,
        report.evidence_version, report.envelope_hash, sender.agent_id,
        receiver.agent_id, report.created_at)
    outbox.plan(envelope, route=ROUTE, target_type="AGENT",
                target_id=receiver.agent_id, policy_version="agent-report-v1",
                control_epoch=control_epoch, intent_id="report-report1",
                created_at=START, valid_until=END,
                report_fingerprint=report_fingerprint if fingerprint else None)
    attempt = outbox.claim(envelope, route=ROUTE, target_type="AGENT",
                           target_id=receiver.agent_id, claim_token="token",
                           claim_at=START + timedelta(seconds=1),
                           lease_until=END)
    inbox = UpstreamInbox()
    if inbox_epoch:
        # A separate accepted report's revocation advances the inbox epoch.
        prior = replace(to_sector_report(report), report_id="earlier")
        prior_receipt = inbox.accept(prior, now=START)
        inbox.revoke(prior_receipt.receipt_id, now=START)
    if accept:
        inbox.accept(to_sector_report(report),
                     now=START + timedelta(seconds=accepted_seconds))
    upstream = inbox.receipt_for_report(report.report_id)
    ack_id = upstream.ack.ack_id if upstream else "self-filled-ack"
    source = upstream.ack.verification_source if upstream else "self-filled-source"
    settled = outbox.finish(
        attempt.attempt_id, outcome=ACCEPTED_VERIFIED,
        external_message_id=ack_id, verification_source=source,
        observed_at=START + timedelta(seconds=observed_seconds),
        now=START + timedelta(seconds=5))
    assert settled.outcome == ACCEPTED_VERIFIED
    store = EvidenceStore()
    store.freeze(envelope)
    pool = SignalPool(store)
    pool.append(SignalPoolDraft("signal1", "ANOMALY", envelope.event_id,
                                envelope.envelope_hash, envelope.evidence_version),
                producer=sender, store=store, now=START)
    pool.claim("signal1", lease_until=END, now=START)
    acceptance = AcceptanceReceipt(ROUTE, attempt.attempt_id, ack_id)
    adapter = TrustedAcceptanceBindingAdapter(
        outbox, envelope, report, inbox, now=START + timedelta(seconds=5))
    return envelope, report, outbox, inbox, pool, acceptance, adapter


def _reject(chain) -> None:
    """Verify the pool stays claimed on a failed lookup."""
    _, _, _, _, pool, acceptance, adapter = chain
    assert adapter.binding_for(acceptance.attempt_id) is None
    with pytest.raises(SignalPoolError, match="RECEIPT_ATTEMPT_UNKNOWN"):
        pool.mark_accepted("signal1", receipt=acceptance, binding_port=adapter)
    assert pool.get("signal1").status == "CLAIMED"


def test_authoritative_live_acceptance_is_read_only_and_offline() -> None:
    chain = _chain()
    _, _, outbox, inbox, pool, receipt, adapter = chain
    attempts_before = dict(outbox._attempts)
    receipts_before = dict(inbox._receipts)
    assert adapter.production_permitted is False
    assert adapter.notification_permitted is False
    assert adapter.trading_permitted is False
    assert adapter.binding_for(receipt.attempt_id).external_message_id == receipt.external_ref
    assert outbox._attempts == attempts_before
    assert inbox._receipts == receipts_before
    assert pool.mark_accepted("signal1", receipt=receipt,
                              binding_port=adapter).status == "ACCEPTED"


def test_outbox_self_filled_verified_without_inbox_must_reject() -> None:
    _reject(_chain(accept=False))


def test_revoked_trusted_receipt_must_reject() -> None:
    chain = _chain()
    _, report, _, inbox, _, _, _ = chain
    inbox.revoke(inbox.receipt_for_report(report.report_id).receipt_id,
                 now=START + timedelta(seconds=6))
    _reject(chain)


def test_wrong_payload_hash_and_report_mapping_must_reject() -> None:
    for altered in ("envelope_hash", "report_kind", "sender_agent_id"):
        chain = _chain()
        _, report, _, inbox, _, _, _ = chain
        original = inbox._reports[report.report_id]
        changes = {"envelope_hash": "f" * 64} if altered == "envelope_hash" else {
            altered: "HEALTH" if altered == "report_kind" else "imposter"}
        if altered == "envelope_hash":
            receipt = inbox.receipt_for_report(report.report_id)
            inbox._receipts[receipt.receipt_id] = replace(receipt, **changes)
        else:
            inbox._reports[report.report_id] = replace(original, **changes)
        _reject(chain)


def test_wrong_ack_id_receiver_source_and_time_must_reject() -> None:
    for changes in ({"ack_id": "fake-ack"},
                    {"receiver_agent_id": "imposter"},
                    {"verification_source": "fake-source"},
                    {"acked_at": START + timedelta(seconds=4)}):
        chain = _chain()
        _, report, _, inbox, _, _, _ = chain
        receipt = inbox.receipt_for_report(report.report_id)
        inbox._receipts[receipt.receipt_id] = replace(
            receipt, ack=replace(receipt.ack, **changes))
        _reject(chain)


def test_wrong_outbox_receipt_source_and_time_must_reject() -> None:
    for changes in ({"verification_source": "fake-source"},
                    {"observed_at": START + timedelta(seconds=4)}):
        chain = _chain()
        _, _, outbox, _, _, acceptance, _ = chain
        attempt = outbox._attempts[acceptance.attempt_id]
        outbox._attempts[acceptance.attempt_id] = replace(
            attempt, receipt=replace(attempt.receipt, **changes))
        _reject(chain)


def test_wrong_report_id_and_identity_must_reject() -> None:
    chain = _chain()
    envelope, report, outbox, inbox, pool, acceptance, _ = chain
    adapter = TrustedAcceptanceBindingAdapter(
        outbox, envelope, replace(report, report_id="different"), inbox,
        now=START + timedelta(seconds=5))
    _reject((envelope, report, outbox, inbox, pool, acceptance, adapter))
    adapter = TrustedAcceptanceBindingAdapter(
        outbox, envelope, replace(report, sender=replace(report.sender,
                                                         agent_id="imposter")), inbox,
        now=START + timedelta(seconds=5))
    _reject((envelope, report, outbox, inbox, pool, acceptance, adapter))


def test_missing_report_fingerprint_must_reject() -> None:
    _reject(_chain(fingerprint=False))


def test_mismatched_epoch_and_stale_inbox_epoch_must_reject() -> None:
    _reject(_chain(control_epoch=1))
    _reject(_chain(inbox_epoch=1))
    chain = _chain()
    _, _, _, inbox, _, _, _ = chain
    earlier = replace(to_sector_report(chain[1]), report_id="earlier")
    old = inbox.accept(earlier, now=START + timedelta(seconds=6))
    inbox.revoke(old.receipt_id, now=START + timedelta(seconds=7))
    _reject(chain)


def test_time_order_and_agent_lifetime_must_reject() -> None:
    chain = _chain()
    _, report, _, inbox, _, _, _ = chain
    receipt = inbox.receipt_for_report(report.report_id)
    inbox._receipts[receipt.receipt_id] = replace(
        receipt, accepted_at=START - timedelta(seconds=1))
    _reject(chain)
    _reject(_chain(accepted_seconds=4, observed_seconds=3))
    chain = _chain()
    envelope, report, outbox, inbox, pool, acceptance, _ = chain
    bad = replace(report, receiver=replace(report.receiver,
                                           valid_until=START + timedelta(seconds=2)))
    adapter = TrustedAcceptanceBindingAdapter(
        outbox, envelope, bad, inbox, now=START + timedelta(seconds=5))
    _reject((envelope, report, outbox, inbox, pool, acceptance, adapter))
