"""G1 cross-generation ACK live-inbox authority adversarial tests."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from quant_backtest.agent_bridge.upstream_ack_authority import UpstreamAckAuthority
from quant_backtest.quant_sector.upstream_inbox import QuantUpstreamReport, UpstreamInbox
from quant_backtest.watch_dispatch import RouteOutbox, freeze_offline
from quant_backtest.watch_dispatch.agent_reporting import (
    AgentAck, AgentIdentity, AgentUpstreamReporter,
)
from quant_backtest.watch_dispatch.route_outbox import (
    ACCEPTED_VERIFIED, UNKNOWN, OutboxError,
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


def scenario(authority=True):
    env = envelope()
    inbox = UpstreamInbox()
    box = RouteOutbox(UpstreamAckAuthority(inbox) if authority else None)
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
def test_same_generation_finish_before_claim_fails_and_at_claim_succeeds(now):
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
    assert finished.outcome == (UNKNOWN if now < at(6) else ACCEPTED_VERIFIED)
    assert (finished.receipt is None) == (now < at(6))


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
