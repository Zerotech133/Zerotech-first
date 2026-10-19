"""Offline adversarial tests for the diagnostic-only dynamic attempt inspector."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from quant_backtest.agent_bridge.dynamic_acceptance_inspection import (
    AttemptInspection, DynamicAttemptInspector,
)
from quant_backtest.agent_bridge.mapping import to_sector_report
from quant_backtest.agent_bridge.trusted_acceptance import TrustedAcceptanceBindingAdapter
from quant_backtest.agent_bridge.upstream_ack_authority import UpstreamAckAuthority
from quant_backtest.quant_sector.upstream_inbox import UpstreamInbox
from quant_backtest.watch_dispatch import freeze_offline
from quant_backtest.watch_dispatch.agent_reporting import AgentIdentity, AgentReport
from quant_backtest.watch_dispatch.route_outbox import (
    ACCEPTED_VERIFIED, UNKNOWN, ReportFingerprint, RouteOutbox,
)
from quant_backtest.watch_dispatch.signal_pool import ReceiptBindingPort

START = datetime(2026, 10, 19, 2, 0, tzinfo=timezone.utc)
END = START + timedelta(hours=1)
ROUTE = "QUANT_UPSTREAM_REPORT"


def at(seconds: int) -> datetime:
    """Return a deterministic UTC test instant."""
    return START + timedelta(seconds=seconds)


class FakeClock:
    """Mutable local simulation clock; not a trusted time root."""

    def __init__(self, value: object) -> None:
        self.value = value
        self.calls = 0

    def __call__(self) -> object:
        """Read the current simulated clock value exactly once per call."""
        self.calls += 1
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


def chain(*, report_time: datetime = START, accept_time: datetime | None = None,
          observed_time: datetime | None = None, active_port: bool = False,
          first_unknown: bool = False):
    """Build independent offline outbox and sector-inbox ledgers."""
    envelope = freeze_offline(
        schema_version=1, event_id="event-dynamic", evidence_version=1,
        previous_version_hash=None, pool="B", tenant_or_owner_id="owner1",
        market="SH", code="600000", source_membership_id="member1",
        source_revision="rev1", rule_id="r1", rule_version=1,
        comparator="GE", threshold="1.00", unit="ratio", window="5m",
        observed_value="1.10", baseline_value="1.00", raw_ref="raw:1",
        raw_digest="a" * 64, source_id="src1", calendar_id="cal1",
        source_as_of=START, visible_at=START, detected_at=START,
        freeze_at=START, valid_until=END, facts=("observed",),
        hypotheses=("hypothesis",), supporting_refs=("raw:1",),
        contradicting_refs=(), confirmation_condition="condition",
        invalidation_condition="invalid")
    sender = AgentIdentity("watch1", "WATCH", "registry:watch",
                           START - timedelta(days=1), END)
    receiver = AgentIdentity("sector1", "QUANT_SECTOR", "registry:sector",
                             START - timedelta(days=1), END)
    report = AgentReport("report1", "ANOMALY", envelope.event_id,
                         envelope.evidence_version, envelope.envelope_hash,
                         sender, receiver, report_time)
    inbox = UpstreamInbox()
    outbox = RouteOutbox(active_ack_port=UpstreamAckAuthority(inbox)
                         if active_port else None)
    fingerprint = ReportFingerprint(
        report.report_id, report.report_kind, report.event_id,
        report.evidence_version, report.envelope_hash, sender.agent_id,
        receiver.agent_id, report.created_at)
    outbox.plan(envelope, route=ROUTE, target_type="AGENT",
                target_id=receiver.agent_id, policy_version="agent-report-v1",
                control_epoch=0, intent_id="report-report1",
                created_at=report_time, valid_until=END,
                report_fingerprint=fingerprint)
    attempt = outbox.claim(envelope, route=ROUTE, target_type="AGENT",
                           target_id=receiver.agent_id, claim_token="token",
                           claim_at=report_time + timedelta(seconds=1),
                           lease_until=END)
    receipt = inbox.accept(to_sector_report(report),
                           now=accept_time if accept_time is not None else at(3))
    if first_unknown:
        outbox.finish(attempt.attempt_id, outcome=UNKNOWN,
                      now=report_time + timedelta(seconds=5))
    else:
        outbox.finish(attempt.attempt_id, outcome=ACCEPTED_VERIFIED,
                      external_message_id=receipt.ack.ack_id,
                      verification_source=receipt.ack.verification_source,
                      observed_at=observed_time if observed_time is not None
                      else receipt.ack.acked_at,
                      now=report_time + timedelta(seconds=5))
    return outbox, envelope, report, inbox, attempt.attempt_id


def test_each_inspection_samples_once_and_old_adapter_does_not_refresh() -> None:
    """A new adapter is composed for each query; earlier clock is not cached."""
    outbox, envelope, report, inbox, attempt_id = chain()
    clock = FakeClock(at(2))
    inspector = DynamicAttemptInspector(outbox, envelope, report, inbox,
                                        clock=clock)
    old_adapter = TrustedAcceptanceBindingAdapter(
        outbox, envelope, report, inbox, now=at(2))
    assert old_adapter.binding_for(attempt_id) is None
    first = inspector.inspect(attempt_id)
    assert first == AttemptInspection(attempt_id, at(2), "UNAVAILABLE")
    clock.value = at(6)
    second = inspector.inspect(attempt_id)
    assert second == AttemptInspection(attempt_id, at(6), "CONSISTENT_OFFLINE")
    assert clock.calls == 2
    assert old_adapter.binding_for(attempt_id) is None
    with pytest.raises(FrozenInstanceError):
        second.status = "UNAVAILABLE"
    assert ReceiptBindingPort not in type(inspector).__mro__
    assert not hasattr(inspector, "binding_for")
    for result in (second, inspector):
        assert result.production_permitted is False
        assert result.notification_permitted is False
        assert result.trading_permitted is False


def test_revocation_after_consistent_query_fails_closed() -> None:
    """Previously reported consistency is not immutable permission."""
    outbox, envelope, report, inbox, attempt_id = chain()
    clock = FakeClock(at(6))
    inspector = DynamicAttemptInspector(outbox, envelope, report, inbox,
                                        clock=clock)
    assert inspector.inspect(attempt_id).status == "CONSISTENT_OFFLINE"
    receipt = inbox.receipt_for_report(report.report_id)
    inbox.revoke(receipt.receipt_id, now=at(7))
    clock.value = at(8)
    assert inspector.inspect(attempt_id) == AttemptInspection(
        attempt_id, at(8), "UNAVAILABLE")
    assert clock.calls == 2


@pytest.mark.parametrize("invalid", [None, "2026-10-19T02:00:06Z",
                                     datetime(2026, 10, 19, 2, 0),
                                     datetime(2026, 10, 19, 10, 0,
                                              tzinfo=timezone(timedelta(hours=8))),
                                     RuntimeError("clock unavailable")])
def test_bad_clock_is_unavailable_without_time(invalid: object) -> None:
    """Clock failures and even non-zero UTC offsets fail closed."""
    outbox, envelope, report, inbox, attempt_id = chain()
    clock = FakeClock(invalid)
    inspector = DynamicAttemptInspector(outbox, envelope, report, inbox,
                                        clock=clock)
    assert inspector.inspect(attempt_id) == AttemptInspection(
        attempt_id, None, "UNAVAILABLE")
    assert clock.calls == 1


def test_unknown_attempt_and_invalid_constructor_inputs_fail_closed() -> None:
    """A clock reading is retained when exact-attempt lookup fails."""
    outbox, envelope, report, inbox, attempt_id = chain()
    clock = FakeClock(at(6))
    inspector = DynamicAttemptInspector(outbox, envelope, report, inbox,
                                        clock=clock)
    for unknown in ("missing", "", None):
        assert inspector.inspect(unknown) == AttemptInspection(
            unknown, at(6), "UNAVAILABLE")
    broken = DynamicAttemptInspector(None, envelope, report, inbox,
                                     clock=clock)
    assert broken.inspect(attempt_id) == AttemptInspection(
        attempt_id, at(6), "UNAVAILABLE")
    assert clock.calls == 4


def test_future_report_accepted_too_early_is_unavailable() -> None:
    """Inbox acceptance alone does not establish report creation chronology."""
    outbox, envelope, report, inbox, attempt_id = chain(
        report_time=at(3), accept_time=at(1), observed_time=at(1))
    inspector = DynamicAttemptInspector(outbox, envelope, report, inbox,
                                        clock=FakeClock(at(9)))
    assert inspector.inspect(attempt_id) == AttemptInspection(
        attempt_id, at(9), "UNAVAILABLE")


def test_old_live_ack_before_new_generation_claim_remains_consistent() -> None:
    """An observed_at earlier than retry claim is not itself a contradiction."""
    outbox, envelope, report, inbox, initial_id = chain(
        active_port=True, first_unknown=True)
    key = outbox._key_for(envelope, ROUTE, "AGENT", report.receiver.agent_id)
    first = outbox._attempts[initial_id]
    assert first.outcome == UNKNOWN
    outbox.plan(envelope, route=ROUTE, target_type="AGENT",
                target_id=report.receiver.agent_id,
                policy_version="agent-report-v1", control_epoch=0,
                intent_id="report-report1-g1", generation=1,
                created_at=at(6), valid_until=END,
                report_fingerprint=outbox._report_fingerprints[key])
    retry = outbox.claim(envelope, route=ROUTE, target_type="AGENT",
                         target_id=report.receiver.agent_id,
                         claim_token="retry", claim_at=at(7), lease_until=END)
    ack = inbox.receipt_for_report(report.report_id).ack
    settled = outbox.finish(
        retry.attempt_id, outcome=ACCEPTED_VERIFIED,
        external_message_id=ack.ack_id,
        verification_source=ack.verification_source,
        observed_at=ack.acked_at, now=at(8))
    assert settled.receipt is not None
    assert settled.receipt.observed_at < retry.claim_at
    inspector = DynamicAttemptInspector(outbox, envelope, report, inbox,
                                        clock=FakeClock(at(9)))
    assert inspector.inspect(retry.attempt_id).status == "CONSISTENT_OFFLINE"
