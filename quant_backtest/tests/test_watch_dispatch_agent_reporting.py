"""Agent-to-agent upstream reporting tests. Watch reports only to its sector Agent."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from quant_backtest.watch_dispatch import RouteOutbox, freeze_offline
from quant_backtest.watch_dispatch.agent_reporting import (
    AgentAck, AgentIdentity, AgentReportingError, AgentUpstreamReporter,
)

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


def test_report_resolves_sector_agent_and_binds_intent():
    outbox = RouteOutbox()
    envelope = sample()
    rep = reporter()
    report = rep.report(envelope, outbox, report_kind="ANOMALY",
                        receiver_agent_id="quant-sector-agent-1",
                        report_id="r-1", now=T)
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
                               report_id="r-h", now=T)
    assert report.report_kind == "HEALTH"
    with pytest.raises(AgentReportingError, match="REPORT_KIND_INVALID"):
        reporter().report(envelope, RouteOutbox(), report_kind="DIRECT_FEISHU",
                          receiver_agent_id="quant-sector-agent-1",
                          report_id="r-bad", now=T)
    with pytest.raises(AgentReportingError, match="REPORT_KIND_INVALID"):
        reporter().report(envelope, RouteOutbox(), report_kind="TRADE_ORDER",
                          receiver_agent_id="quant-sector-agent-1",
                          report_id="r-bad2", now=T)


def test_unresolved_or_wrong_role_receiver_refused_before_planning():
    envelope = sample()
    outbox = RouteOutbox()
    with pytest.raises(AgentReportingError, match="AGENT_UNRESOLVED"):
        reporter(EmptyDirectory()).report(envelope, outbox, report_kind="ANOMALY",
                                          receiver_agent_id="quant-sector-agent-1",
                                          report_id="r-1", now=T)
    watch_only = MemoryDirectory(watch_agent())
    with pytest.raises(AgentReportingError, match="AGENT_UNRESOLVED"):
        reporter(watch_only).report(envelope, outbox, report_kind="ANOMALY",
                                    receiver_agent_id="watch-agent-1",
                                    report_id="r-2", now=T)
    stale_dir = MemoryDirectory(sector_agent(valid_until=T))
    with pytest.raises(AgentReportingError, match="AGENT_NOT_CURRENT"):
        reporter(stale_dir).report(envelope, outbox, report_kind="ANOMALY",
                                   receiver_agent_id="quant-sector-agent-1",
                                   report_id="r-3", now=T)
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
            receiver_agent_id="quant-sector-agent-1", report_id="r-4", now=T)


def test_self_report_rejected_before_any_intent_is_planned():
    envelope = sample()
    outbox = RouteOutbox()
    self_dir = MemoryDirectory(sector_agent(agent_id="watch-agent-1"))
    rep = reporter(self_dir)
    with pytest.raises(AgentReportingError, match="AGENT_SELF_REPORT_FORBIDDEN"):
        rep.report(envelope, outbox, report_kind="ANOMALY",
                   receiver_agent_id="watch-agent-1", report_id="r-self", now=T)
    assert outbox.get_intent(envelope, route="QUANT_UPSTREAM_REPORT",
                             target_type="AGENT", target_id="watch-agent-1") is None
    assert outbox.get_attempts(envelope, route="QUANT_UPSTREAM_REPORT",
                               target_type="AGENT", target_id="watch-agent-1") == ()


def test_ack_verified_before_finish_and_bindings_enforced():
    envelope = sample()
    outbox = RouteOutbox()
    rep = reporter()
    report = rep.report(envelope, outbox, report_kind="ANOMALY",
                        receiver_agent_id="quant-sector-agent-1",
                        report_id="r-1", now=T)
    good = ack(report)
    attempt, finished = rep.claim_and_confirm(
        envelope, outbox, report, claim_token="tok-1",
        claim_at=T + timedelta(seconds=1),
        lease_until=LATER - timedelta(minutes=5), ack=good,
        now=T + timedelta(seconds=40))
    assert finished is not None and finished.outcome == "ACCEPTED_VERIFIED"
    assert finished.receipt.external_message_id == "ack-1"
    assert finished.receipt.observed_at == T + timedelta(seconds=30)
    hostile = ack(report, receiver_agent_id="other-agent")
    envelope2 = sample(event_id="ev2")
    report2 = rep.report(envelope2, outbox, report_kind="ANOMALY",
                         receiver_agent_id="quant-sector-agent-1",
                         report_id="r-2", now=T)
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
