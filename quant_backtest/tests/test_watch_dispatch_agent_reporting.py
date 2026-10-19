"""Agent-to-agent upstream reporting tests. Watch reports only to its sector Agent."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from quant_backtest.agent_bridge.upstream_ack_authority import UpstreamAckAuthority
from quant_backtest.quant_sector.upstream_inbox import QuantUpstreamReport, UpstreamInbox
from quant_backtest.watch_dispatch import RouteOutbox, freeze_offline
from quant_backtest.watch_dispatch.agent_reporting import (
    AgentAck, AgentIdentity, AgentRejectionEvidence, AgentReportingError,
    AgentUpstreamReporter,
)
from quant_backtest.watch_dispatch.route_outbox import OutboxError, ReportFingerprint

T = datetime(2026, 10, 19, 2, 0, tzinfo=timezone.utc)
H = "a" * 64
LATER = T + timedelta(minutes=30)


def sample(**changes):
    fields = dict(schema_version=1, event_id="ev1", evidence_version=1,
                  previous_version_hash=None, pool="B", tenant_or_owner_id="owner1",
                  market="SH", code="600000", source_membership_id="member1",
                  source_revision="rev1", rule_id="r1", rule_version=1,
                  comparator="GE", threshold="1.00", unit="ratio", window="5m",
                  observed_value="1.10", baseline_value="1.00", raw_ref="raw:1",
                  raw_digest=H, source_id="src1", calendar_id="cal1",
                  source_as_of=T, visible_at=T, detected_at=T, freeze_at=T,
                  valid_until=LATER, facts=("observed",), hypotheses=("c",),
                  supporting_refs=("raw:1",), contradicting_refs=(),
                  confirmation_condition="c", invalidation_condition="i")
    fields.update(changes)
    return freeze_offline(**fields)


def watch_agent(**changes):
    fields = dict(agent_id="watch-agent-1", role="WATCH", registry_ref="registry:watch:1",
                  registered_at=T - timedelta(days=1), valid_until=LATER)
    fields.update(changes)
    return AgentIdentity(**fields)


def sector_agent(**changes):
    fields = dict(agent_id="quant-sector-agent-1", role="QUANT_SECTOR",
                  registry_ref="registry:quant:1", registered_at=T - timedelta(days=1),
                  valid_until=LATER)
    fields.update(changes)
    return AgentIdentity(**fields)


class MemoryDirectory:
    def __init__(self, *agents):
        self.agents = agents

    def resolve(self, agent_id, *, role, now):
        for agent in self.agents:
            if agent.agent_id == agent_id and agent.role == role:
                return agent
        return None


class EmptyDirectory(MemoryDirectory):
    def resolve(self, agent_id, *, role, now):
        return None


class MemoryAckPort:
    def verify(self, ack, report, now):
        if (ack.report_id == report.report_id
                and ack.receiver_agent_id == report.receiver.agent_id):
            return ack
        return None


def reporter(directory=None, ack=None):
    return AgentUpstreamReporter(watch_agent(),
                                 directory or MemoryDirectory(sector_agent()),
                                 ack or MemoryAckPort())


def ack(report, **changes):
    fields = dict(ack_id="ack-1", report_id=report.report_id,
                  receiver_agent_id=report.receiver.agent_id,
                  acked_at=T + timedelta(seconds=30), verification_source="agent-ack:1")
    fields.update(changes)
    return AgentAck(**fields)


def accepted_ack(inbox, report, *, at):
    """Issue an ACK through the independent sector inbox, not caller fields."""
    upstream = QuantUpstreamReport(
        report_id=report.report_id, report_kind=report.report_kind,
        event_id=report.event_id, evidence_hash=report.envelope_hash,
        evidence_version=report.evidence_version,
        sender_agent_id=report.sender.agent_id,
        receiver_agent_id=report.receiver.agent_id, created_at=report.created_at)
    issued = inbox.accept(upstream, now=at).ack
    return AgentAck(issued.ack_id, issued.report_id, issued.receiver_agent_id,
                    issued.acked_at, issued.verification_source)


def test_report_requires_epoch_and_rejects_stale_epoch():
    env = sample()
    outbox = RouteOutbox()
    rep = reporter()
    kwargs = dict(report_kind="ANOMALY", receiver_agent_id="quant-sector-agent-1",
                  report_id="r-epoch", now=T)
    with pytest.raises(TypeError, match="control_epoch"):
        rep.report(env, outbox, **kwargs)
    outbox.revoke_envelope(env, epoch=2, now=T)
    assert outbox.epoch_floor(env) == 2
    with pytest.raises(OutboxError, match="EPOCH_STALE"):
        rep.report(env, outbox, control_epoch=2, **kwargs)
    assert outbox.get_intent(env, route="QUANT_UPSTREAM_REPORT",
                             target_type="AGENT", target_id="quant-sector-agent-1") is None
    report = rep.report(env, outbox, control_epoch=outbox.epoch_floor(env) + 1,
                        **kwargs)
    assert report.report_id == "r-epoch"


class MemoryRejectionPort:
    def __init__(self, evidence):
        self.evidence = evidence

    def verify_rejection(self, report, *, now):
        return self.evidence


def test_rejection_port_optional_and_verified_rejection_reopens_intent():
    env = sample()

    class BoundRejection:
        def active_rejection(self, fingerprint, attempt, intent, identity,
                             generation, rejection_ref, verification_source,
                             observed_at, *, now):
            expected = ReportFingerprint("r-rej", "ANOMALY", env.event_id,
                                         env.evidence_version, env.envelope_hash,
                                         "watch-agent-1", "quant-sector-agent-1", T)
            return (fingerprint == expected and attempt.intent_id == intent.intent_id
                    and intent.intent_id == "report-r-rej-g1"
                    and intent.control_epoch == 0 and generation == 1
                    and identity == RouteOutbox._identity(env)
                    and rejection_ref == "revoke-1"
                    and verification_source == "trusted-ledger"
                    and attempt.claim_at <= observed_at <= now)

    rejection_authority = BoundRejection()
    assert not isinstance(rejection_authority, MemoryRejectionPort)
    outbox = RouteOutbox(active_rejection_port=rejection_authority)
    rep = reporter()
    report = rep.report(env, outbox, report_kind="ANOMALY",
                        receiver_agent_id="quant-sector-agent-1", report_id="r-rej",
                        control_epoch=0, now=T)
    invalid_ack = ack(report, report_id="other")
    _, unknown = rep.claim_and_confirm(
        env, outbox, report, claim_token="bad", claim_at=T + timedelta(seconds=1),
        lease_until=LATER - timedelta(minutes=5), ack=invalid_ack,
        now=T + timedelta(seconds=4))
    assert unknown.outcome == "UNKNOWN"
    with pytest.raises(AgentReportingError, match="RETRY_NOT_PERMITTED"):
        rep.retry_report(env, outbox, report, generation=0, control_epoch=0,
                         now=T + timedelta(seconds=5))
    retry = rep.retry_report(env, outbox, report, generation=1, control_epoch=0,
                             now=T + timedelta(seconds=5))
    accepting = AgentUpstreamReporter(
        watch_agent(), MemoryDirectory(sector_agent()), MemoryAckPort(),
        MemoryRejectionPort(AgentRejectionEvidence(
            rejection_ref="revoke-1", verification_source="trusted-ledger",
            observed_at=T + timedelta(seconds=7))))
    _, rejected = accepting.claim_and_confirm(
        env, outbox, retry, claim_token="rejected",
        claim_at=T + timedelta(seconds=7), lease_until=LATER - timedelta(minutes=5),
        ack=invalid_ack, now=T + timedelta(seconds=8))
    assert rejected.outcome == "DEFINITELY_REJECTED"
    assert rejected.receipt.external_message_id == "revoke-1"
    assert outbox.get_intent(env, route="QUANT_UPSTREAM_REPORT",
                             target_type="AGENT", target_id="quant-sector-agent-1").status == "READY_OFFLINE"
    attempt = outbox.claim(env, route="QUANT_UPSTREAM_REPORT", target_type="AGENT",
                           target_id="quant-sector-agent-1", claim_token="third",
                           claim_at=T + timedelta(seconds=9), lease_until=LATER - timedelta(minutes=5))
    assert attempt.attempt_no == 3


@pytest.mark.parametrize("evidence", [
    AgentRejectionEvidence("", "trusted", T + timedelta(seconds=2)),
    AgentRejectionEvidence("ref", "", T + timedelta(seconds=2)),
    AgentRejectionEvidence("ref", "trusted", datetime(2026, 10, 19, 2, 0)),
])
def test_rejection_invalid_evidence_refused(evidence):
    env = sample()
    outbox = RouteOutbox()
    rep = AgentUpstreamReporter(watch_agent(), MemoryDirectory(sector_agent()),
                                MemoryAckPort(), MemoryRejectionPort(evidence))
    report = rep.report(env, outbox, report_kind="ANOMALY",
                        receiver_agent_id="quant-sector-agent-1", report_id="r-rej",
                        control_epoch=0, now=T)
    with pytest.raises(AgentReportingError, match="REJECTION_EVIDENCE_INVALID"):
        rep.claim_and_confirm(
            env, outbox, report, claim_token="bad", claim_at=T + timedelta(seconds=1),
            lease_until=LATER - timedelta(minutes=5),
            ack=ack(report, report_id="other"), now=T + timedelta(seconds=3))


def test_retry_rejects_stale_prior_generation():
    env = sample()
    outbox = RouteOutbox()
    rep = reporter()
    report = rep.report(env, outbox, report_kind="ANOMALY",
                        receiver_agent_id="quant-sector-agent-1", report_id="r-stale",
                        control_epoch=0, now=T)
    rep.claim_and_confirm(
        env, outbox, report, claim_token="bad", claim_at=T + timedelta(seconds=1),
        lease_until=LATER - timedelta(minutes=5),
        ack=ack(report, report_id="other"), now=T + timedelta(seconds=2))
    second = rep.retry_report(env, outbox, report, generation=1,
                              control_epoch=0, now=T + timedelta(seconds=3))
    with pytest.raises(AgentReportingError, match="RETRY_NOT_PERMITTED"):
        rep.retry_report(env, outbox, report, generation=2, control_epoch=0,
                         now=T + timedelta(seconds=4))
    assert second is report
    assert second.created_at == T
    assert outbox.get_intent(env, route="QUANT_UPSTREAM_REPORT",
                             target_type="AGENT", target_id="quant-sector-agent-1").created_at == T + timedelta(seconds=3)


def test_revoked_report_cannot_retry_even_with_new_epoch():
    env = sample()
    outbox = RouteOutbox()
    rep = reporter()
    report = rep.report(env, outbox, report_kind="ANOMALY",
                        receiver_agent_id="quant-sector-agent-1", report_id="r-revoked",
                        control_epoch=0, now=T)
    attempt, _ = rep.claim_and_confirm(
        env, outbox, report, claim_token="bad", claim_at=T + timedelta(seconds=1),
        lease_until=LATER - timedelta(minutes=5),
        ack=ack(report, report_id="other"), now=T + timedelta(seconds=2))
    assert outbox._attempts[attempt.attempt_id].outcome == "UNKNOWN"
    outbox.revoke_envelope(env, epoch=1, now=T + timedelta(seconds=3))
    with pytest.raises(AgentReportingError, match="RETRY_NOT_PERMITTED"):
        rep.retry_report(env, outbox, report, generation=1, control_epoch=2,
                         now=T + timedelta(seconds=4))
    assert outbox.epoch_floor(env) == 1


def test_retry_requires_existing_unknown_attempt():
    env = sample()
    inbox = UpstreamInbox()
    outbox = RouteOutbox(active_ack_port=UpstreamAckAuthority(inbox))
    rep = reporter()
    report = rep.report(env, outbox, report_kind="ANOMALY",
                        receiver_agent_id="quant-sector-agent-1", report_id="r-retry",
                        control_epoch=0, now=T)
    with pytest.raises(AgentReportingError, match="RETRY_NOT_PERMITTED"):
        rep.retry_report(env, outbox, report, generation=1, control_epoch=0,
                         now=T + timedelta(seconds=2))
    rep.claim_and_confirm(
        env, outbox, report, claim_token="good", claim_at=T + timedelta(seconds=1),
        lease_until=LATER - timedelta(minutes=5),
        ack=accepted_ack(inbox, report, at=T + timedelta(seconds=30)),
        now=T + timedelta(seconds=40))
    with pytest.raises(AgentReportingError, match="RETRY_NOT_PERMITTED"):
        rep.retry_report(env, outbox, report, generation=1, control_epoch=0,
                         now=T + timedelta(seconds=41))


def test_report_resolves_sector_agent_and_binds_intent():
    outbox = RouteOutbox()
    envelope = sample()
    rep = reporter()
    report = rep.report(envelope, outbox, report_kind="ANOMALY",
                        receiver_agent_id="quant-sector-agent-1",
                        report_id="r-1", control_epoch=0, now=T)
    assert report.report_kind == "ANOMALY"
    assert report.sender.agent_id == "watch-agent-1"
    assert report.receiver.agent_id == "quant-sector-agent-1"
    assert report.non_executable is True
    intent = outbox.get_intent(envelope, route="QUANT_UPSTREAM_REPORT",
                               target_type="AGENT", target_id="quant-sector-agent-1")
    assert intent is not None and intent.status == "READY_OFFLINE"
    assert intent.executable is False
    for item in (report, rep):
        assert item.trading_permitted is False
        assert item.dispatch_permitted is False
        assert item.notification_permitted is False


def test_health_problem_also_reports_upward():
    outbox = RouteOutbox()
    envelope = sample()
    report = reporter().report(envelope, outbox, report_kind="HEALTH",
                               receiver_agent_id="quant-sector-agent-1",
                               report_id="r-h", control_epoch=0, now=T)
    assert report.report_kind == "HEALTH"
    with pytest.raises(AgentReportingError, match="REPORT_KIND_INVALID"):
        reporter().report(envelope, RouteOutbox(), report_kind="DIRECT_FEISHU",
                          receiver_agent_id="quant-sector-agent-1",
                          report_id="r-bad", control_epoch=0, now=T)
    with pytest.raises(AgentReportingError, match="REPORT_KIND_INVALID"):
        reporter().report(envelope, RouteOutbox(), report_kind="TRADE_ORDER",
                          receiver_agent_id="quant-sector-agent-1",
                          report_id="r-bad2", control_epoch=0, now=T)


def test_unresolved_or_wrong_role_receiver_refused_before_planning():
    envelope = sample()
    outbox = RouteOutbox()
    with pytest.raises(AgentReportingError, match="AGENT_UNRESOLVED"):
        reporter(EmptyDirectory()).report(envelope, outbox, report_kind="ANOMALY",
                                          receiver_agent_id="quant-sector-agent-1",
                                          report_id="r-1", control_epoch=0, now=T)
    watch_only = MemoryDirectory(watch_agent())
    with pytest.raises(AgentReportingError, match="AGENT_UNRESOLVED"):
        reporter(watch_only).report(envelope, outbox, report_kind="ANOMALY",
                                    receiver_agent_id="watch-agent-1",
                                    report_id="r-2", control_epoch=0, now=T)
    stale_dir = MemoryDirectory(sector_agent(valid_until=T))
    with pytest.raises(AgentReportingError, match="AGENT_NOT_CURRENT"):
        reporter(stale_dir).report(envelope, outbox, report_kind="ANOMALY",
                                   receiver_agent_id="quant-sector-agent-1",
                                   report_id="r-3", control_epoch=0, now=T)
    assert outbox.get_intent(envelope, route="QUANT_UPSTREAM_REPORT",
                             target_type="AGENT",
                             target_id="quant-sector-agent-1") is None


def test_sender_must_be_watch_and_current():
    with pytest.raises(AgentReportingError, match="SENDER_MUST_BE_WATCH_AGENT"):
        AgentUpstreamReporter(sector_agent(), MemoryDirectory(sector_agent()),
                              MemoryAckPort())
    expired = watch_agent(valid_until=T)
    with pytest.raises(AgentReportingError, match="SENDER_NOT_CURRENT"):
        AgentUpstreamReporter(expired, MemoryDirectory(sector_agent()),
                              MemoryAckPort()).report(
            sample(), RouteOutbox(), report_kind="ANOMALY",
            receiver_agent_id="quant-sector-agent-1", report_id="r-4", control_epoch=0, now=T)


def test_self_report_rejected_before_any_intent_is_planned():
    envelope = sample()
    outbox = RouteOutbox()
    self_dir = MemoryDirectory(sector_agent(agent_id="watch-agent-1"))
    rep = reporter(self_dir)
    with pytest.raises(AgentReportingError, match="AGENT_SELF_REPORT_FORBIDDEN"):
        rep.report(envelope, outbox, report_kind="ANOMALY",
                   receiver_agent_id="watch-agent-1", report_id="r-self", control_epoch=0, now=T)
    assert outbox.get_intent(envelope, route="QUANT_UPSTREAM_REPORT",
                             target_type="AGENT", target_id="watch-agent-1") is None
    assert outbox.get_attempts(envelope, route="QUANT_UPSTREAM_REPORT",
                               target_type="AGENT", target_id="watch-agent-1") == ()


@pytest.mark.parametrize("tamper", [
    lambda report: replace(report, report_id="forged"),
    lambda report: replace(report, report_kind="HEALTH"),
    lambda report: replace(report, created_at=T + timedelta(seconds=1)),
    lambda report: replace(report, receiver=sector_agent(registry_ref="forged")),
    lambda report: replace(report, receiver=sector_agent(agent_id="other-sector")),
    lambda report: replace(report, sender=watch_agent(registry_ref="forged")),
])
def test_forged_report_rejected_before_claim_without_spending_attempt(tamper):
    envelope = sample()
    inbox = UpstreamInbox()
    outbox = RouteOutbox(active_ack_port=UpstreamAckAuthority(inbox))
    rep = reporter()
    original = rep.report(envelope, outbox, report_kind="ANOMALY",
                          receiver_agent_id="quant-sector-agent-1",
                          report_id="r-original", control_epoch=0, now=T)
    good = accepted_ack(inbox, original, at=T + timedelta(seconds=30))
    forged = tamper(original)
    intent = outbox.get_intent(envelope, route="QUANT_UPSTREAM_REPORT",
                               target_type="AGENT", target_id=original.receiver.agent_id)
    with pytest.raises(AgentReportingError, match="REPORT_.*MISMATCH|AGENT_NOT_CURRENT"):
        rep.claim_and_confirm(
            envelope, outbox, forged, claim_token="forged",
            claim_at=T + timedelta(seconds=2), lease_until=LATER - timedelta(minutes=5),
            ack=ack(forged), now=T + timedelta(seconds=40))
    assert outbox.get_intent(envelope, route="QUANT_UPSTREAM_REPORT",
                             target_type="AGENT", target_id=original.receiver.agent_id) == intent
    assert outbox.get_attempts(envelope, route="QUANT_UPSTREAM_REPORT",
                               target_type="AGENT", target_id=original.receiver.agent_id) == ()
    assert outbox.get_attempts(envelope, route="QUANT_UPSTREAM_REPORT",
                               target_type="AGENT", target_id=forged.receiver.agent_id) == ()
    _, finished = rep.claim_and_confirm(
        envelope, outbox, original, claim_token="real",
        claim_at=T + timedelta(seconds=2), lease_until=LATER - timedelta(minutes=5),
        ack=good, now=T + timedelta(seconds=40))
    assert finished.outcome == "ACCEPTED_VERIFIED"
    assert outbox.get_attempts(envelope, route="QUANT_UPSTREAM_REPORT",
                               target_type="AGENT", target_id=original.receiver.agent_id)[0].attempt_no == 1


@pytest.mark.parametrize("identity_change", [
    lambda report: replace(report, receiver=sector_agent(valid_until=T + timedelta(seconds=5))),
    lambda report: replace(report, sender=watch_agent(valid_until=T + timedelta(seconds=5))),
])
def test_agent_registry_expiry_tamper_never_claims(identity_change):
    envelope = sample()
    outbox = RouteOutbox()
    rep = reporter()
    original = rep.report(envelope, outbox, report_kind="ANOMALY",
                          receiver_agent_id="quant-sector-agent-1",
                          report_id="r-expiry", control_epoch=0, now=T)
    with pytest.raises(AgentReportingError, match="REPORT_SENDER_MISMATCH|AGENT_NOT_CURRENT"):
        rep.claim_and_confirm(
            envelope, outbox, identity_change(original), claim_token="bad",
            claim_at=T + timedelta(seconds=6), lease_until=LATER - timedelta(minutes=5),
            ack=ack(original), now=T + timedelta(seconds=7))
    assert outbox.get_attempts(envelope, route="QUANT_UPSTREAM_REPORT",
                               target_type="AGENT", target_id=original.receiver.agent_id) == ()


def test_direct_cross_generation_intent_id_drift_refused_before_claim():
    envelope = sample()
    outbox = RouteOutbox()
    rep = reporter()
    report = rep.report(envelope, outbox, report_kind="ANOMALY",
                        receiver_agent_id="quant-sector-agent-1",
                        report_id="r-generations", control_epoch=0, now=T)
    _, unknown = rep.claim_and_confirm(
        envelope, outbox, report, claim_token="first",
        claim_at=T + timedelta(seconds=1), lease_until=LATER - timedelta(minutes=5),
        ack=ack(report, report_id="not-matching"), now=T + timedelta(seconds=2))
    assert unknown.outcome == "UNKNOWN"
    key = outbox._key_for(envelope, "QUANT_UPSTREAM_REPORT", "AGENT",
                          report.receiver.agent_id)
    outbox.plan(
        envelope, route="QUANT_UPSTREAM_REPORT", target_type="AGENT",
        target_id=report.receiver.agent_id, policy_version="agent-report-v1",
        control_epoch=0, intent_id="report-unrelated-g1",
        created_at=T + timedelta(seconds=3), valid_until=envelope.valid_until,
        generation=1, report_fingerprint=outbox._report_fingerprints[key])
    current = outbox.get_intent(envelope, route="QUANT_UPSTREAM_REPORT",
                                target_type="AGENT", target_id=report.receiver.agent_id)
    with pytest.raises(AgentReportingError, match="REPORT_IDENTITY_MISMATCH"):
        rep.claim_and_confirm(
            envelope, outbox, report, claim_token="drift",
            claim_at=T + timedelta(seconds=4), lease_until=LATER - timedelta(minutes=5),
            ack=ack(report, acked_at=T + timedelta(seconds=5)),
            now=T + timedelta(seconds=6))
    assert outbox.get_intent(envelope, route="QUANT_UPSTREAM_REPORT",
                             target_type="AGENT", target_id=report.receiver.agent_id) == current
    assert len(outbox.get_attempts(envelope, route="QUANT_UPSTREAM_REPORT",
                                   target_type="AGENT", target_id=report.receiver.agent_id)) == 1
    assert outbox._attempt_nos[key] == 1


def test_valid_generation_one_uses_original_report_and_current_window():
    envelope = sample()
    inbox = UpstreamInbox()
    outbox = RouteOutbox(active_ack_port=UpstreamAckAuthority(inbox))
    rep = reporter()
    report = rep.report(envelope, outbox, report_kind="ANOMALY",
                        receiver_agent_id="quant-sector-agent-1",
                        report_id="r-valid-g1", control_epoch=0, now=T)
    rep.claim_and_confirm(
        envelope, outbox, report, claim_token="first",
        claim_at=T + timedelta(seconds=1), lease_until=LATER - timedelta(minutes=5),
        ack=ack(report, report_id="not-matching"), now=T + timedelta(seconds=2))
    retry = rep.retry_report(envelope, outbox, report, generation=1,
                             control_epoch=0, now=T + timedelta(seconds=3))
    assert retry is report
    current = outbox.get_intent(envelope, route="QUANT_UPSTREAM_REPORT",
                                target_type="AGENT", target_id=report.receiver.agent_id)
    forged = replace(retry, report_id="different-generation")
    with pytest.raises(AgentReportingError, match="REPORT_IDENTITY_MISMATCH"):
        rep.claim_and_confirm(
            envelope, outbox, forged, claim_token="forged-g1",
            claim_at=T + timedelta(seconds=4), lease_until=LATER - timedelta(minutes=5),
            ack=ack(forged, acked_at=T + timedelta(seconds=5)),
            now=T + timedelta(seconds=6))
    assert outbox.get_intent(envelope, route="QUANT_UPSTREAM_REPORT",
                             target_type="AGENT", target_id=report.receiver.agent_id) == current
    assert len(outbox.get_attempts(envelope, route="QUANT_UPSTREAM_REPORT",
                                   target_type="AGENT", target_id=report.receiver.agent_id)) == 1
    good = accepted_ack(inbox, retry, at=T + timedelta(seconds=5))
    second, finished = rep.claim_and_confirm(
        envelope, outbox, retry, claim_token="second",
        claim_at=T + timedelta(seconds=4), lease_until=LATER - timedelta(minutes=5),
        ack=good, now=T + timedelta(seconds=6))
    assert second.intent_id == "report-r-valid-g1-g1"
    assert second.attempt_no == 2
    assert finished.outcome == "ACCEPTED_VERIFIED"


def test_ack_verified_before_finish_and_bindings_enforced():
    envelope = sample()
    inbox = UpstreamInbox()
    outbox = RouteOutbox(active_ack_port=UpstreamAckAuthority(inbox))
    rep = reporter()
    report = rep.report(envelope, outbox, report_kind="ANOMALY",
                        receiver_agent_id="quant-sector-agent-1",
                        report_id="r-1", control_epoch=0, now=T)
    good = accepted_ack(inbox, report, at=T + timedelta(seconds=30))
    attempt, finished = rep.claim_and_confirm(
        envelope, outbox, report, claim_token="tok-1",
        claim_at=T + timedelta(seconds=1),
        lease_until=LATER - timedelta(minutes=5), ack=good,
        now=T + timedelta(seconds=40))
    assert finished is not None and finished.outcome == "ACCEPTED_VERIFIED"
    assert finished.receipt.external_message_id == good.ack_id
    assert finished.receipt.observed_at == T + timedelta(seconds=30)
    hostile = ack(report, receiver_agent_id="other-agent")
    envelope2 = sample(event_id="ev2")
    report2 = rep.report(envelope2, outbox, report_kind="ANOMALY",
                         receiver_agent_id="quant-sector-agent-1",
                         report_id="r-2", control_epoch=0, now=T)
    attempt2, finished2 = rep.claim_and_confirm(
        envelope2, outbox, report2, claim_token="tok-2",
        claim_at=T + timedelta(seconds=1),
        lease_until=LATER - timedelta(minutes=5), ack=hostile,
        now=T + timedelta(seconds=40))
    assert finished2.outcome == "UNKNOWN" and finished2.receipt is None
    with pytest.raises(AgentReportingError, match="REPORT_SENDER_MISMATCH"):
        other_reporter = AgentUpstreamReporter(
            watch_agent(agent_id="watch-agent-2"), MemoryDirectory(sector_agent()),
            MemoryAckPort())
        other_reporter.claim_and_confirm(envelope, outbox, report,
                                         claim_token="tok-3",
                                         claim_at=T + timedelta(seconds=1),
                                         lease_until=LATER - timedelta(minutes=5),
                                         ack=good, now=T + timedelta(seconds=40))
