"""G1 cross-generation ACK live-inbox authority adversarial tests."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Callable

import pytest

from quant_backtest.agent_bridge.upstream_ack_authority import UpstreamAckAuthority
from quant_backtest.quant_sector.upstream_inbox import (
    QuantUpstreamReport, UpstreamAck, UpstreamInbox,
)
from quant_backtest.watch_dispatch import RouteOutbox, freeze_offline
from quant_backtest.watch_dispatch.contracts import EvidenceEnvelope
from quant_backtest.watch_dispatch.agent_reporting import (
    AgentAck, AgentIdentity, AgentUpstreamReporter,
)
from quant_backtest.watch_dispatch.route_outbox import (
    ACCEPTED_VERIFIED, UNKNOWN, OutboxError, ReportFingerprint, RouteAttempt,
)

T = datetime(2026, 10, 19, 2, tzinfo=timezone.utc)
END = T + timedelta(minutes=30)


def at(seconds: int) -> datetime:
    return T + timedelta(seconds=seconds)


def envelope():
    return freeze_offline(
        schema_version=1, event_id="ev1", evidence_version=1,
        previous_version_hash=None, pool="B", tenant_or_owner_id="owner1",
        market="SH", code="600000", source_membership_id="member1",
        source_revision="rev1", rule_id="r1", rule_version=1,
        comparator="GE", threshold="1.00", unit="ratio", window="5m",
        observed_value="1.10", baseline_value="1.00", raw_ref="raw:1",
        raw_digest="a" * 64, source_id="src1", calendar_id="cal1",
        source_as_of=T, visible_at=T, detected_at=T, freeze_at=T,
        valid_until=END, facts=("observed",), hypotheses=("c",),
        supporting_refs=("raw:1",), contradicting_refs=(),
        confirmation_condition="c", invalidation_condition="i")


class Directory:
    def __init__(self, sector):
        self.sector = sector

    def resolve(self, agent_id, *, role, now):
        return self.sector if agent_id == self.sector.agent_id and role == self.sector.role else None


class AckPort:
    """Report binding only; NOT live acceptance authority."""

    def verify(self, ack, report, now):
        return ack if (ack.report_id == report.report_id
                       and ack.receiver_agent_id == report.receiver.agent_id) else None


class ChangedAckPort:
    def verify(self, ack, report, now):
        return replace(ack, ack_id="other-ack")


class RaisingAuthority:
    def active_ack(self, fingerprint, ack_id, verification_source, acked_at, *, now):
        raise RuntimeError("upstream unavailable")


class MutatingActiveAckPort:
    """Exercise ledger changes after the genuine inbox ACK passes verification."""

    def __init__(self, authority: UpstreamAckAuthority,
                 after_verified: Callable[[], None]) -> None:
        self.authority = authority
        self.after_verified = after_verified
        self.verified_calls = 0

    def active_ack(self, fingerprint: ReportFingerprint, ack_id: str,
                   verification_source: str, acked_at: datetime, *,
                   now: datetime) -> bool:
        if self.authority.active_ack(fingerprint, ack_id, verification_source,
                                     acked_at, now=now) is not True:
            return False
        self.verified_calls += 1
        self.after_verified()
        return True


def scenario(authority: bool = True,
             after_verified: Callable[[RouteOutbox, EvidenceEnvelope], None] | None = None):
    env = envelope()
    inbox = UpstreamInbox()
    if after_verified is None:
        port = UpstreamAckAuthority(inbox) if authority else None
    else:
        # The port is bound at construction, not replaced after the claim.
        port = MutatingActiveAckPort(
            UpstreamAckAuthority(inbox), lambda: after_verified(box, env))
    box = RouteOutbox(port)
    sender = AgentIdentity("watch-1", "WATCH", "registry:watch", T - timedelta(days=1), END)
    receiver = AgentIdentity("sector-1", "QUANT_SECTOR", "registry:sector", T - timedelta(days=1), END)
    reporter = AgentUpstreamReporter(sender, Directory(receiver), AckPort())
    report = reporter.report(env, box, report_kind="ANOMALY", receiver_agent_id="sector-1",
                             report_id="up-1", control_epoch=0, now=T)
    sector = QuantUpstreamReport(report.report_id, report.report_kind,
                                 report.event_id, report.envelope_hash,
                                 report.evidence_version, sender.agent_id,
                                 receiver.agent_id, report.created_at)
    receipt = inbox.accept(sector, now=at(2))
    ack = AgentAck(receipt.ack.ack_id, receipt.ack.report_id,
                   receipt.ack.receiver_agent_id, receipt.ack.acked_at,
                   receipt.ack.verification_source)
    first, unknown = reporter.claim_and_confirm(
        env, box, report, claim_token="first", claim_at=at(1), lease_until=at(20),
        ack=replace(ack, report_id="invalid"), now=at(3))
    assert first.outcome == "CLAIMED" and unknown.outcome == UNKNOWN
    reporter.retry_report(env, box, report, generation=1, control_epoch=0, now=at(5))
    return env, inbox, box, reporter, report, receipt, ack, first


def confirm(s, *, ack=None, now=None, lease=None):
    env, _, box, reporter, report, _, genuine, _ = s
    return reporter.claim_and_confirm(env, box, report, claim_token="second",
                                      claim_at=at(6), lease_until=lease or at(20),
                                      ack=ack or genuine, now=now or at(8))[1]


def test_g1_old_ack_requires_live_exact_inbox_and_preserves_unknown_attempt():
    s = scenario()
    result = confirm(s)
    assert result.outcome == ACCEPTED_VERIFIED
    assert result.receipt.observed_at == at(2)
    assert s[2]._attempts[s[-1].attempt_id].outcome == UNKNOWN
    assert s[2].production_permitted is False


@pytest.mark.parametrize("change", [
    {"ack_id": "forged"}, {"verification_source": "fake"},
    {"acked_at": at(3)}, {"receiver_agent_id": "wrong"},
    {"report_id": "wrong"},
])
def test_forged_ack_never_accepts(change):
    s = scenario()
    assert confirm(s, ack=replace(s[6], **change)).outcome == UNKNOWN


def test_missing_inbox_receipt_and_no_bound_port_fail_closed():
    s = scenario()
    s[1]._by_report.clear()
    assert confirm(s).outcome == UNKNOWN
    assert confirm(scenario(authority=False)).outcome == UNKNOWN


def test_different_report_payload_sender_and_watch_hash_are_not_sector_hash():
    s = scenario()
    receipt = s[5]
    assert receipt.envelope_hash != s[4].envelope_hash
    s[1]._receipts[receipt.receipt_id] = replace(receipt, envelope_hash="b" * 64)
    assert confirm(s).outcome == UNKNOWN
    s = scenario()
    receipt = s[5]
    s[1]._receipts[receipt.receipt_id] = replace(receipt, sender_agent_id="different")
    assert confirm(s).outcome == UNKNOWN


def test_revoked_sector_receipt_and_outbox_epoch_fail_closed():
    s = scenario()
    s[1].revoke(s[5].receipt_id, now=at(4))
    assert confirm(s).outcome == UNKNOWN
    s = scenario()
    env, _, box = s[:3]
    box._epochs[box._identity(env)] = 1
    with pytest.raises(OutboxError, match="INTENT_REVOKED"):
        confirm(s)


def test_epoch_changed_after_claim_denies_old_ack_at_finish():
    s = scenario()
    env, _, box, _, report, _, ack, _ = s
    attempt = box.claim(env, route="QUANT_UPSTREAM_REPORT", target_type="AGENT",
                        target_id=report.receiver.agent_id, claim_token="second",
                        claim_at=at(6), lease_until=at(20))
    box._epochs[box._identity(env)] = 1
    finished = box.finish(attempt.attempt_id, outcome=ACCEPTED_VERIFIED,
                          now=at(8), external_message_id=ack.ack_id,
                          verification_source=ack.verification_source,
                          observed_at=ack.acked_at)
    assert finished.outcome == UNKNOWN and finished.receipt is None


def test_ack_before_original_report_and_in_future_rejected():
    s = scenario()
    assert confirm(s, ack=replace(s[6], acked_at=T - timedelta(seconds=1))).outcome == UNKNOWN
    s = scenario()
    receipt = s[5]
    s[1]._receipts[receipt.receipt_id] = replace(
        receipt, accepted_at=T - timedelta(seconds=1))
    assert confirm(s).outcome == UNKNOWN
    s = scenario()
    assert UpstreamAckAuthority(s[1]).active_ack(
        s[2]._report_fingerprints[next(iter(s[2]._report_fingerprints))],
        s[6].ack_id, s[6].verification_source, s[6].acked_at, now=at(1)) is False


def test_finish_before_claim_is_unknown_without_mutating_old_attempt():
    s = scenario()
    env, _, box, _, report, _, ack, first = s
    second = box.claim(env, route="QUANT_UPSTREAM_REPORT", target_type="AGENT",
                       target_id=report.receiver.agent_id, claim_token="second",
                       claim_at=at(6), lease_until=at(20))
    finished = box.finish(second.attempt_id, outcome=ACCEPTED_VERIFIED,
                          now=at(5) + timedelta(milliseconds=500),
                          external_message_id=ack.ack_id,
                          verification_source=ack.verification_source,
                          observed_at=ack.acked_at)
    assert finished.outcome == UNKNOWN and finished.receipt is None
    assert box._attempts[first.attempt_id].outcome == UNKNOWN


@pytest.mark.parametrize("now", [at(5) + timedelta(milliseconds=500), at(6)])
def test_same_generation_finish_without_authority_is_unknown_even_at_claim(now):
    env = envelope()
    box = RouteOutbox()
    box.plan(env, route="QUANT_UPSTREAM_REPORT", target_type="AGENT",
             target_id="sector-1", policy_version="agent-report-v1",
             control_epoch=0, intent_id="same-generation", created_at=T,
             valid_until=END)
    attempt = box.claim(env, route="QUANT_UPSTREAM_REPORT", target_type="AGENT",
                        target_id="sector-1", claim_token="first", claim_at=at(6),
                        lease_until=at(20))
    finished = box.finish(attempt.attempt_id, outcome=ACCEPTED_VERIFIED,
                          now=now, external_message_id="ack-1",
                          verification_source="test-only", observed_at=at(5))
    assert finished.outcome == UNKNOWN
    assert finished.receipt is None


def same_generation_scenario(
        after_verified: Callable[[RouteOutbox, EvidenceEnvelope], None] | None = None
) -> tuple[EvidenceEnvelope, UpstreamInbox, RouteOutbox, UpstreamAck, RouteAttempt]:
    """Build a first-generation intent with its original fingerprint and live ACK."""
    env = envelope()
    inbox = UpstreamInbox()
    if after_verified is None:
        port = UpstreamAckAuthority(inbox)
    else:
        port = MutatingActiveAckPort(
            UpstreamAckAuthority(inbox), lambda: after_verified(box, env))
    box = RouteOutbox(port)
    fingerprint = ReportFingerprint(
        report_id="same-generation-report", report_kind="ANOMALY",
        event_id=env.event_id, evidence_version=env.evidence_version,
        envelope_hash=env.envelope_hash, sender_agent_id="watch-1",
        receiver_agent_id="sector-1", created_at=T)
    box.plan(env, route="QUANT_UPSTREAM_REPORT", target_type="AGENT",
             target_id="sector-1", policy_version="agent-report-v1",
             control_epoch=0, intent_id="same-generation", created_at=T,
             valid_until=END, report_fingerprint=fingerprint)
    report = QuantUpstreamReport(
        report_id=fingerprint.report_id, report_kind=fingerprint.report_kind,
        event_id=fingerprint.event_id, evidence_hash=fingerprint.envelope_hash,
        evidence_version=fingerprint.evidence_version,
        sender_agent_id=fingerprint.sender_agent_id,
        receiver_agent_id=fingerprint.receiver_agent_id,
        created_at=fingerprint.created_at)
    ack = inbox.accept(report, now=at(5)).ack
    attempt = box.claim(env, route="QUANT_UPSTREAM_REPORT", target_type="AGENT",
                        target_id="sector-1", claim_token="first", claim_at=at(6),
                        lease_until=at(20))
    return env, inbox, box, ack, attempt


def test_same_generation_live_ack_before_claim_accepts_at_claim():
    # Both ledgers here are local/offline; this does not establish a production source.
    _, _, box, ack, attempt = same_generation_scenario()
    finished = box.finish(attempt.attempt_id, outcome=ACCEPTED_VERIFIED,
                          now=at(6), external_message_id=ack.ack_id,
                          verification_source=ack.verification_source,
                          observed_at=ack.acked_at)
    assert finished.outcome == ACCEPTED_VERIFIED
    assert finished.receipt is not None
    assert finished.receipt.observed_at == at(5)


@pytest.mark.parametrize("generation", ["same", "cross"])
def test_callback_revoke_after_real_ack_preserves_revocation(generation: str) -> None:
    """A true ACK must not overwrite a revoke executed within its own callback."""
    mutation: Callable[[RouteOutbox, EvidenceEnvelope], None] = (
        lambda box, env: box.revoke_envelope(
            env, epoch=box.epoch_floor(env) + 1, now=at(8)))
    if generation == "same":
        env, _, box, ack, attempt = same_generation_scenario(mutation)
    else:
        env, _, box, _, report, _, ack, _ = scenario(after_verified=mutation)
        attempt = box.claim(env, route="QUANT_UPSTREAM_REPORT", target_type="AGENT",
                            target_id=report.receiver.agent_id, claim_token="second",
                            claim_at=at(6), lease_until=at(20))
    initial_epoch = box.epoch_floor(env)
    port = box._active_ack_port
    assert isinstance(port, MutatingActiveAckPort)
    result = box.finish(attempt.attempt_id, outcome=ACCEPTED_VERIFIED,
                        now=at(8), external_message_id=ack.ack_id,
                        verification_source=ack.verification_source,
                        observed_at=ack.acked_at)
    stored = box._attempts[attempt.attempt_id]
    intent = box.get_intent(env, route="QUANT_UPSTREAM_REPORT", target_type="AGENT",
                            target_id="sector-1")
    assert port.verified_calls == 1
    assert result == stored and stored.outcome == UNKNOWN and stored.receipt is None
    assert intent is not None and intent.status == UNKNOWN
    assert box.epoch_floor(env) == initial_epoch + 1
    assert attempt.idempotency_key in box._revoked
    assert box._identity(env) in box._revoked_identities
    assert not box.production_permitted and not box.notification_permitted
    assert not box.trading_permitted


@pytest.mark.parametrize("generation", ["same", "cross"])
def test_callback_epoch_drift_after_real_ack_is_stored_unknown(generation: str) -> None:
    """Epoch drift without a revoke still settles both ledgers as UNKNOWN."""
    def advance_epoch(box: RouteOutbox, env: EvidenceEnvelope) -> None:
        box._epochs[box._identity(env)] = box.epoch_floor(env) + 1

    if generation == "same":
        env, _, box, ack, attempt = same_generation_scenario(advance_epoch)
    else:
        env, _, box, _, report, _, ack, _ = scenario(after_verified=advance_epoch)
        attempt = box.claim(env, route="QUANT_UPSTREAM_REPORT", target_type="AGENT",
                            target_id=report.receiver.agent_id, claim_token="second",
                            claim_at=at(6), lease_until=at(20))
    initial_epoch = box.epoch_floor(env)
    port = box._active_ack_port
    assert isinstance(port, MutatingActiveAckPort)
    result = box.finish(attempt.attempt_id, outcome=ACCEPTED_VERIFIED,
                        now=at(8), external_message_id=ack.ack_id,
                        verification_source=ack.verification_source,
                        observed_at=ack.acked_at)
    stored = box._attempts[attempt.attempt_id]
    intent = box.get_intent(env, route="QUANT_UPSTREAM_REPORT", target_type="AGENT",
                            target_id="sector-1")
    assert port.verified_calls == 1
    assert result == stored and stored.outcome == UNKNOWN and stored.receipt is None
    assert intent is not None and intent.status == UNKNOWN
    assert box.epoch_floor(env) == initial_epoch + 1
    assert attempt.idempotency_key not in box._revoked
    assert not box.production_permitted and not box.notification_permitted
    assert not box.trading_permitted


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="current_gap: inbox ACK revocation during active_ack is not atomic with outbox.finish",
)
@pytest.mark.parametrize("generation", ["same", "cross"])
def test_inbox_revoke_during_real_ack_must_not_publish_verified_receipt(
        generation: str) -> None:
    """A revoked inbox receipt must not settle as verified in either generation."""
    revoked_receipt_ids: list[str] = []

    def revoke_after_verified(box: RouteOutbox, env: EvidenceEnvelope) -> None:
        port = box._active_ack_port
        if not isinstance(port, MutatingActiveAckPort):
            raise RuntimeError("REAL_ACK_PORT_MISSING")
        inbox = port.authority._inbox
        report_id = "same-generation-report" if generation == "same" else "up-1"
        receipt = inbox.receipt_for_report(report_id)
        if receipt is None:
            raise RuntimeError("UPSTREAM_RECEIPT_MISSING")
        inbox.revoke(receipt.receipt_id, now=at(8))
        revoked_receipt_ids.append(receipt.receipt_id)

    if generation == "same":
        env, inbox, box, ack, attempt = same_generation_scenario(revoke_after_verified)
    else:
        env, inbox, box, _, report, _, ack, _ = scenario(
            after_verified=revoke_after_verified)
        attempt = box.claim(env, route="QUANT_UPSTREAM_REPORT", target_type="AGENT",
                            target_id=report.receiver.agent_id, claim_token="second",
                            claim_at=at(6), lease_until=at(20))
    port = box._active_ack_port
    assert isinstance(port, MutatingActiveAckPort)
    result = box.finish(attempt.attempt_id, outcome=ACCEPTED_VERIFIED,
                        now=at(8), external_message_id=ack.ack_id,
                        verification_source=ack.verification_source,
                        observed_at=ack.acked_at)
    if port.verified_calls != 1 or len(revoked_receipt_ids) != 1:
        raise RuntimeError("REAL_ACK_CALLBACK_NOT_COMPLETED")
    if not inbox.is_revoked(ack.report_id) or inbox.is_active(revoked_receipt_ids[0]):
        raise RuntimeError("INBOX_REVOCATION_NOT_COMMITTED")
    if box.production_permitted or box.notification_permitted or box.trading_permitted:
        raise RuntimeError("OUTBOX_PERMISSION_UNEXPECTED")
    assert result.outcome == UNKNOWN and result.receipt is None, (
        f"revoked upstream receipt settled as {result.outcome}, "
        f"outbox_receipt={result.receipt is not None}")
    assert box._attempts[attempt.attempt_id] == result


def test_cross_generation_finish_at_claim_preserves_old_ack_time():
    s = scenario()
    env, _, box, _, report, _, ack, _ = s
    attempt = box.claim(env, route="QUANT_UPSTREAM_REPORT", target_type="AGENT",
                        target_id=report.receiver.agent_id, claim_token="second",
                        claim_at=at(6), lease_until=at(20))
    finished = box.finish(attempt.attempt_id, outcome=ACCEPTED_VERIFIED,
                          now=at(6), external_message_id=ack.ack_id,
                          verification_source=ack.verification_source,
                          observed_at=ack.acked_at)
    assert finished.outcome == ACCEPTED_VERIFIED
    assert finished.receipt.observed_at == at(2)


def test_lease_expiration_and_authority_exception_become_unknown():
    s = scenario()
    assert confirm(s, now=at(20)).outcome == UNKNOWN
    s = scenario()
    s[2]._active_ack_port = RaisingAuthority()
    assert confirm(s).outcome == UNKNOWN


def test_ack_verifier_must_return_the_exact_supplied_ack():
    s = scenario()
    s[3]._ack_port = ChangedAckPort()
    assert confirm(s).outcome == UNKNOWN


def test_live_authority_rejects_wrong_fingerprint_and_ack_time():
    s = scenario()
    fingerprint = next(iter(s[2]._report_fingerprints.values()))
    authority = UpstreamAckAuthority(s[1])
    assert not authority.active_ack(replace(fingerprint, report_id="other"),
                                    s[6].ack_id, s[6].verification_source,
                                    s[6].acked_at, now=at(8))
    assert not authority.active_ack(replace(fingerprint, sender_agent_id="other"),
                                    s[6].ack_id, s[6].verification_source,
                                    s[6].acked_at, now=at(8))
    assert not authority.active_ack(fingerprint, s[6].ack_id,
                                    s[6].verification_source, at(3), now=at(8))
