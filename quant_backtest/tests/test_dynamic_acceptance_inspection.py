"""Offline adversarial tests for the diagnostic-only dynamic attempt inspector."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone

import pytest

from quant_backtest.agent_bridge.dynamic_acceptance_inspection import (
    AttemptInspection, DynamicAttemptInspector,
)
from quant_backtest.agent_bridge.mapping import to_sector_report
from quant_backtest.agent_bridge.trusted_acceptance import TrustedAcceptanceBindingAdapter
from quant_backtest.agent_bridge.upstream_ack_authority import UpstreamAckAuthority
from quant_backtest.quant_sector.upstream_inbox import UpstreamInbox, UpstreamInboxError
from quant_backtest.watch_dispatch import freeze_offline
from quant_backtest.watch_dispatch.agent_reporting import AgentIdentity, AgentReport
from quant_backtest.watch_dispatch.route_outbox import (
    ACCEPTED_VERIFIED, UNKNOWN, ReportFingerprint, RouteOutbox,
)
from quant_backtest.watch_dispatch.signal_pool import AttemptBinding, ReceiptBindingPort

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
          observed_time: datetime | None = None, active_port: bool = True,
          first_unknown: bool = False, accept_report: bool = True):
    """Build offline ledgers, binding the live local ACK port by default."""
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
    if accept_report:
        receipt = inbox.accept(to_sector_report(report),
                               now=accept_time if accept_time is not None else at(3))
    else:
        receipt = None
    if first_unknown or receipt is None:
        settled = outbox.finish(attempt.attempt_id, outcome=UNKNOWN,
                                now=report_time + timedelta(seconds=5))
        assert settled.outcome == UNKNOWN and settled.receipt is None
    else:
        settled = outbox.finish(
            attempt.attempt_id, outcome=ACCEPTED_VERIFIED,
            external_message_id=receipt.ack.ack_id,
            verification_source=receipt.ack.verification_source,
            observed_at=observed_time if observed_time is not None
            else receipt.ack.acked_at,
            now=report_time + timedelta(seconds=5))
        if active_port:
            assert settled.outcome == ACCEPTED_VERIFIED
            assert settled.receipt is not None
        else:
            assert settled.outcome == UNKNOWN and settled.receipt is None
    return outbox, envelope, report, inbox, attempt.attempt_id


def assert_inspection_unavailable(inspector: DynamicAttemptInspector,
                                  attempt_id: str, checked_at: datetime | None) -> None:
    """Require an exact unavailable result with every permission denied."""
    result = inspector.inspect(attempt_id)
    assert result == AttemptInspection(attempt_id, checked_at, "UNAVAILABLE")
    assert result.production_permitted is False
    assert result.notification_permitted is False
    assert result.trading_permitted is False
    assert inspector.production_permitted is False
    assert inspector.notification_permitted is False
    assert inspector.trading_permitted is False


def test_each_inspection_samples_once_and_old_adapter_does_not_refresh() -> None:
    """A new adapter is composed for each query; earlier clock is not cached."""
    outbox, envelope, report, inbox, attempt_id = chain()
    clock = FakeClock(at(2))
    inspector = DynamicAttemptInspector(outbox, envelope, report, inbox,
                                        clock=clock)
    old_adapter = TrustedAcceptanceBindingAdapter(
        outbox, envelope, report, inbox, now=at(2))
    assert old_adapter.binding_for(attempt_id) is None
    assert_inspection_unavailable(inspector, attempt_id, at(2))
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


def test_revocation_removes_offline_consistency() -> None:
    """Previously reported consistency is not immutable permission."""
    outbox, envelope, report, inbox, attempt_id = chain()
    clock = FakeClock(at(6))
    inspector = DynamicAttemptInspector(outbox, envelope, report, inbox,
                                        clock=clock)
    consistent = inspector.inspect(attempt_id)
    assert consistent == AttemptInspection(attempt_id, at(6), "CONSISTENT_OFFLINE")
    assert consistent.production_permitted is False
    assert consistent.notification_permitted is False
    assert consistent.trading_permitted is False
    receipt = inbox.receipt_for_report(report.report_id)
    assert receipt is not None
    inbox.revoke(receipt.receipt_id, now=at(7))
    clock.value = at(8)
    assert_inspection_unavailable(inspector, attempt_id, at(8))
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
    assert_inspection_unavailable(inspector, attempt_id, None)
    assert clock.calls == 1


def test_unknown_attempt_and_invalid_constructor_inputs_fail_closed() -> None:
    """A clock reading is retained when exact-attempt lookup fails."""
    outbox, envelope, report, inbox, attempt_id = chain()
    clock = FakeClock(at(6))
    inspector = DynamicAttemptInspector(outbox, envelope, report, inbox,
                                        clock=clock)
    for unknown in ("missing", "", None):
        assert_inspection_unavailable(inspector, unknown, at(6))
    broken = DynamicAttemptInspector(None, envelope, report, inbox,
                                     clock=clock)
    assert_inspection_unavailable(broken, attempt_id, at(6))
    assert clock.calls == 4


def test_wrong_attempt_binding_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even an exact binding type for another attempt cannot pass inspection."""
    outbox, envelope, report, inbox, attempt_id = chain()
    original = TrustedAcceptanceBindingAdapter.binding_for

    def wrong_binding(adapter: TrustedAcceptanceBindingAdapter,
                      requested_id: str) -> AttemptBinding | None:
        """Substitute a foreign attempt identity without changing binding shape."""
        binding = original(adapter, requested_id)
        if binding is None:
            return None
        return replace(binding, attempt_id="different-attempt")

    monkeypatch.setattr(TrustedAcceptanceBindingAdapter, "binding_for",
                        wrong_binding)
    inspector = DynamicAttemptInspector(outbox, envelope, report, inbox,
                                        clock=FakeClock(at(6)))
    assert_inspection_unavailable(inspector, attempt_id, at(6))


def test_future_report_accepted_too_early_is_unavailable() -> None:
    """A future-dated report cannot create an inbox receipt or permission."""
    outbox, envelope, report, inbox, attempt_id = chain(
        report_time=at(3), accept_report=False)
    with pytest.raises(UpstreamInboxError, match="^REPORT_CREATED_AT_FUTURE$"):
        inbox.accept(to_sector_report(report), now=at(1))
    assert inbox.receipt_for_report(report.report_id) is None
    assert inbox.list_accepted() == ()
    assert inbox.ledger_epoch == 0
    inspector = DynamicAttemptInspector(outbox, envelope, report, inbox,
                                        clock=FakeClock(at(9)))
    assert_inspection_unavailable(inspector, attempt_id, at(9))


def test_idempotent_accept_rejects_time_rollback_before_returning_ack() -> None:
    """An existing receipt cannot bypass the report-created-at time gate."""
    outbox, envelope, report, inbox, attempt_id = chain(report_time=at(3))
    original = inbox.receipt_for_report(report.report_id)
    assert original is not None
    with pytest.raises(UpstreamInboxError, match="^REPORT_CREATED_AT_FUTURE$"):
        inbox.accept(to_sector_report(report), now=at(2))
    assert inbox.receipt_for_report(report.report_id) is original
    assert inbox.list_accepted() == (original,)
    assert inbox.ledger_epoch == 0
    inspector = DynamicAttemptInspector(outbox, envelope, report, inbox,
                                        clock=FakeClock(at(2)))
    assert_inspection_unavailable(inspector, attempt_id, at(2))


def test_missing_active_ack_port_cannot_settle_verified() -> None:
    """Matching ACK fields cannot replace a construction-bound authority."""
    outbox, envelope, report, inbox, attempt_id = chain(active_port=False)
    assert inbox.receipt_for_report(report.report_id) is not None
    attempt = outbox._attempts[attempt_id]
    assert attempt.outcome == UNKNOWN and attempt.receipt is None
    inspector = DynamicAttemptInspector(outbox, envelope, report, inbox,
                                        clock=FakeClock(at(9)))
    assert_inspection_unavailable(inspector, attempt_id, at(9))


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
    assert settled.outcome == ACCEPTED_VERIFIED
    assert settled.receipt is not None
    assert settled.receipt.observed_at < retry.claim_at
    inspector = DynamicAttemptInspector(outbox, envelope, report, inbox,
                                        clock=FakeClock(at(9)))
    result = inspector.inspect(retry.attempt_id)
    assert result == AttemptInspection(retry.attempt_id, at(9), "CONSISTENT_OFFLINE")
    assert result.production_permitted is False
    assert result.notification_permitted is False
    assert result.trading_permitted is False
