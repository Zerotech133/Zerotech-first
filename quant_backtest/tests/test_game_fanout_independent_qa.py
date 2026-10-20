"""Independent offline adversarial checks for two-branch internal fanout.

WO-QT-20261020-020: fixture migrated to the mandatory four-port contract.
``signal_lookup`` returns a real SignalPoolEntry whose signal_id is distinct
from the report's evidence event_id and whose producer/kind/event/hash/
version match the report exactly.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from quant_backtest.quant_sector.internal_signal_fanout import (
    FAILED,
    LOGICAL_RECEIVED,
    RESEARCH,
    TRADING,
    InternalFanoutError,
    InternalSignalFanout,
)
from quant_backtest.quant_sector.upstream_inbox import (
    QuantUpstreamReport,
    UpstreamInbox,
)
from quant_backtest.watch_dispatch.signal_pool import (
    SignalPoolEntry,
    STATUS_REVOKED,
)

NOW = datetime(2026, 10, 20, 9, 30, tzinfo=timezone.utc)
SIGNAL_ID = "signal-wo020"
EVENT_ID = "event-wo020"


@pytest.fixture
def scenario():
    inbox = UpstreamInbox()
    report = QuantUpstreamReport(
        report_id="report-independent", report_kind="ANOMALY",
        event_id=EVENT_ID, evidence_hash="a" * 64,
        evidence_version=1, sender_agent_id="watch-agent",
        receiver_agent_id="quant-agent", created_at=NOW,
    )
    receipt = inbox.accept(report, now=NOW)
    entries = {
        SIGNAL_ID: SignalPoolEntry(
            signal_id=SIGNAL_ID, producer_agent_id="watch-agent",
            report_kind="ANOMALY", event_id=EVENT_ID,
            evidence_hash="a" * 64, evidence_version=1, created_at=NOW,
        ),
    }
    allowed = {(RESEARCH, "research-agent"), (TRADING, "trader-agent")}
    fanout = InternalSignalFanout(
        acceptance_lookup=inbox.get,
        acceptance_is_active=inbox.is_active,
        signal_lookup=lambda signal_id, now: entries.get(signal_id),
        consumer_authorized=lambda team, identity, _: (team, identity) in allowed,
    )
    return inbox, report, receipt, allowed, entries, fanout


def plan(fanout, report, receipt, team=RESEARCH, identity="research-agent"):
    return fanout.plan(
        report=report, receipt_id=receipt.receipt_id,
        signal_id=SIGNAL_ID, team=team,
        consumer_identity=identity, now=NOW,
    )


def read(fanout, method, report, team=RESEARCH, identity="research-agent"):
    return getattr(fanout, method)(
        signal_id=SIGNAL_ID, team=team,
        consumer_identity=identity, now=NOW + timedelta(seconds=3),
    )


def test_distinct_signal_id_and_event_id_bind(scenario):
    """signal_id and report.event_id are independent; only the pool entry binds."""
    _, report, _, _, entries, _ = scenario
    assert entries[SIGNAL_ID].signal_id != report.event_id
    assert entries[SIGNAL_ID].event_id == report.event_id


@pytest.mark.parametrize("change", [
    {"report_id": "other-report"},
    {"sender_agent_id": "different-sender"},
    {"receiver_agent_id": "different-receiver"},
    {"event_id": "different-signal"},
    {"evidence_hash": "b" * 64},
    {"evidence_version": 2},
    {"report_kind": "HEALTH"},
])
def test_exact_report_identity_bound_to_live_receipt(scenario, change):
    _, report, receipt, _, _, fanout = scenario
    forged = replace(report, **change)
    with pytest.raises(InternalFanoutError):
        plan(fanout, forged, receipt)
    assert fanout._subscriptions == {}


def test_pool_entry_mismatch_is_rejected(scenario):
    _, report, receipt, _, entries, fanout = scenario
    for mutation in (
        {"producer_agent_id": "other-producer"},
        {"event_id": "mismatched-event"},
        {"evidence_hash": "c" * 64},
        {"evidence_version": 9},
        {"report_kind": "HEALTH"},
    ):
        entries[SIGNAL_ID] = replace(entries[SIGNAL_ID], **mutation)
        with pytest.raises(InternalFanoutError, match="SIGNAL_BINDING_INVALID"):
            plan(fanout, report, receipt)
        assert fanout._subscriptions == {}
    entries[SIGNAL_ID] = replace(
        entries[SIGNAL_ID], producer_agent_id="watch-agent",
        event_id=EVENT_ID, evidence_hash="a" * 64,
        evidence_version=1, report_kind="ANOMALY",
    )


def test_lookup_returning_foreign_receipt_or_false_active_fails_closed(scenario):
    inbox, report, receipt, _, _, _ = scenario
    other = replace(report, report_id="other-report")
    other_receipt = inbox.accept(other, now=NOW)
    for lookup, active in (
        (lambda _: other_receipt, inbox.is_active),
        (inbox.get, lambda _: False),
        (lambda _: (_ for _ in ()).throw(RuntimeError("down")), inbox.is_active),
        (inbox.get, lambda _: (_ for _ in ()).throw(RuntimeError("down"))),
    ):
        fanout = InternalSignalFanout(
            acceptance_lookup=lookup, acceptance_is_active=active,
            signal_lookup=lambda signal_id, now: None,
            consumer_authorized=lambda *_: True,
        )
        with pytest.raises(InternalFanoutError, match="UPSTREAM_NOT_ACTIVE"):
            plan(fanout, report, receipt)
        assert fanout._subscriptions == {}


def test_health_report_never_enters_business_routes(scenario):
    inbox, report, receipt, _, entries, fanout = scenario
    entries[SIGNAL_ID] = replace(entries[SIGNAL_ID], report_kind="HEALTH")
    health = replace(report, report_id="health-report", report_kind="HEALTH")
    health_receipt = inbox.accept(health, now=NOW)
    with pytest.raises(InternalFanoutError, match="BUSINESS_SIGNAL_REQUIRED"):
        plan(fanout, health, health_receipt)
    result = fanout.fanout(
        report=health, receipt_id=health_receipt.receipt_id,
        signal_id=SIGNAL_ID, research_identity="research-agent",
        trading_identity="trader-agent", now=NOW,
    )
    assert result.research is None and result.trading is None
    assert set(code for _, code in result.errors) == {"BUSINESS_SIGNAL_REQUIRED"}


def test_per_branch_permissions_and_claims_cannot_cross_routes(scenario):
    _, report, receipt, allowed, _, fanout = scenario
    result = fanout.fanout(
        report=report, receipt_id=receipt.receipt_id,
        signal_id=SIGNAL_ID, research_identity="research-agent",
        trading_identity="trader-agent", now=NOW,
    )
    assert not result.errors
    research_claim = fanout.claim(
        signal_id=SIGNAL_ID, team=RESEARCH,
        consumer_identity="research-agent", now=NOW + timedelta(seconds=1),
        lease_until=NOW + timedelta(seconds=9),
    )
    with pytest.raises(InternalFanoutError, match="CLAIM_NOT_CURRENT"):
        fanout.finish(
            signal_id=SIGNAL_ID, team=TRADING,
            consumer_identity="trader-agent", claim=research_claim,
            outcome=LOGICAL_RECEIVED, now=NOW + timedelta(seconds=2),
        )
    allowed.add((TRADING, "research-agent"))
    with pytest.raises(InternalFanoutError, match="CONSUMER_MISMATCH"):
        read(fanout, "status", report, TRADING, "research-agent")
    assert read(fanout, "status", report, TRADING, "trader-agent") == "PLANNED"
    assert read(fanout, "consume", report, RESEARCH) is None


@pytest.mark.parametrize("operation", ["plan", "claim", "finish", "consume", "status"])
def test_permission_callback_failure_is_closed_at_each_step(scenario, operation):
    inbox, report, receipt, allowed, _, fanout = scenario
    plan(fanout, report, receipt)
    claim = fanout.claim(
        signal_id=SIGNAL_ID, team=RESEARCH,
        consumer_identity="research-agent", now=NOW + timedelta(seconds=1),
        lease_until=NOW + timedelta(seconds=10),
    )
    def denied(*_):
        raise RuntimeError("authorization backend unavailable")
    fanout._consumer_authorized = denied
    kwargs = dict(signal_id=SIGNAL_ID, team=RESEARCH,
                  consumer_identity="research-agent", now=NOW + timedelta(seconds=2))
    if operation == "plan":
        action = lambda: plan(fanout, report, receipt)
    elif operation == "claim":
        action = lambda: fanout.claim(**kwargs, lease_until=NOW + timedelta(seconds=9))
    elif operation == "finish":
        action = lambda: fanout.finish(**kwargs, claim=claim, outcome=LOGICAL_RECEIVED)
    else:
        action = lambda: getattr(fanout, operation)(**kwargs)
    with pytest.raises(InternalFanoutError, match="CONSUMER_NOT_AUTHORIZED"):
        action()
    assert fanout._responses == {}
    assert inbox.ledger_epoch == 0


def test_revoke_one_receipt_blocks_both_independent_results(scenario):
    inbox, report, receipt, _, _, fanout = scenario
    result = fanout.fanout(
        report=report, receipt_id=receipt.receipt_id,
        signal_id=SIGNAL_ID, research_identity="research-agent",
        trading_identity="trader-agent", now=NOW,
    )
    assert result.research is not None and result.trading is not None
    research_claim = fanout.claim(
        signal_id=SIGNAL_ID, team=RESEARCH,
        consumer_identity="research-agent", now=NOW + timedelta(seconds=1),
        lease_until=NOW + timedelta(seconds=10),
    )
    response = fanout.finish(
        signal_id=SIGNAL_ID, team=RESEARCH,
        consumer_identity="research-agent", claim=research_claim,
        outcome=FAILED, now=NOW + timedelta(seconds=2),
    )
    assert response.outcome == FAILED
    inbox.revoke(receipt.receipt_id, now=NOW + timedelta(seconds=3))
    for team, identity in ((RESEARCH, "research-agent"),
                           (TRADING, "trader-agent")):
        assert read(fanout, "status", report, team, identity) == "REVOKED_OR_INACTIVE"
        with pytest.raises(InternalFanoutError, match="UPSTREAM_NOT_ACTIVE"):
            read(fanout, "consume", report, team, identity)
    assert inbox.ledger_epoch == 1


def test_revoke_in_active_callback_exposes_documented_nonatomic_boundary(scenario):
    """An apparently positive check cannot promise P0 atomic settlement."""
    inbox, report, receipt, _, entries, _ = scenario
    def active_then_revoke(receipt_id):
        was_active = inbox.is_active(receipt_id)
        inbox.revoke(receipt_id, now=NOW)
        return was_active
    fanout = InternalSignalFanout(
        acceptance_lookup=inbox.get,
        acceptance_is_active=active_then_revoke,
        signal_lookup=lambda signal_id, now: entries.get(signal_id),
        consumer_authorized=lambda *_: True,
    )
    subscription = plan(fanout, report, receipt)
    assert subscription.receipt_id == receipt.receipt_id
    assert inbox.is_active(receipt.receipt_id) is False
    assert read(fanout, "status", report) == "REVOKED_OR_INACTIVE"


def test_early_read_does_not_expose_future_planned_state(scenario):
    """As-of readers must not observe a branch before its planned_at."""
    _, report, receipt, _, _, fanout = scenario
    subscription = fanout.plan(
        report=report, receipt_id=receipt.receipt_id,
        signal_id=SIGNAL_ID, team=RESEARCH,
        consumer_identity="research-agent", now=NOW + timedelta(seconds=10),
    )
    assert subscription.planned_at == NOW + timedelta(seconds=10)
    for method in ("status", "consume"):
        with pytest.raises(InternalFanoutError):
            getattr(fanout, method)(
                signal_id=SIGNAL_ID, team=RESEARCH,
                consumer_identity="research-agent", now=NOW + timedelta(seconds=1),
            )
    with pytest.raises(InternalFanoutError):
        fanout.plan(
            report=report, receipt_id=receipt.receipt_id,
            signal_id=SIGNAL_ID, team=RESEARCH,
            consumer_identity="research-agent", now=NOW + timedelta(seconds=1),
        )


def test_early_identical_finish_cannot_return_future_response(scenario):
    """Idempotent re-finish before recorded_at must fail closed."""
    _, report, receipt, _, _, fanout = scenario
    plan(fanout, report, receipt)
    claim = fanout.claim(
        signal_id=SIGNAL_ID, team=RESEARCH,
        consumer_identity="research-agent", now=NOW + timedelta(seconds=1),
        lease_until=NOW + timedelta(seconds=20),
    )
    future = NOW + timedelta(seconds=10)
    response = fanout.finish(
        signal_id=SIGNAL_ID, team=RESEARCH,
        consumer_identity="research-agent", claim=claim,
        outcome=LOGICAL_RECEIVED, now=future,
    )
    with pytest.raises(InternalFanoutError, match="RESPONSE_NOT_VISIBLE"):
        fanout.finish(
            signal_id=SIGNAL_ID, team=RESEARCH,
            consumer_identity="research-agent", claim=claim,
            outcome=LOGICAL_RECEIVED, now=NOW + timedelta(seconds=2),
        )
    assert fanout.finish(
        signal_id=SIGNAL_ID, team=RESEARCH,
        consumer_identity="research-agent", claim=claim,
        outcome=LOGICAL_RECEIVED, now=future,
    ) is response


def test_revoked_pool_entry_blocks_all_later_reads(scenario):
    """A pool-side revocation is as binding as an upstream receipt revoke."""
    _, report, receipt, _, entries, fanout = scenario
    plan(fanout, report, receipt)
    claim = fanout.claim(
        signal_id=SIGNAL_ID, team=RESEARCH,
        consumer_identity="research-agent", now=NOW + timedelta(seconds=1),
        lease_until=NOW + timedelta(seconds=20),
    )
    entries[SIGNAL_ID] = replace(
        entries[SIGNAL_ID], status=STATUS_REVOKED, epoch=1,
    )
    with pytest.raises(InternalFanoutError, match="SIGNAL_BINDING_INVALID"):
        fanout.finish(
            signal_id=SIGNAL_ID, team=RESEARCH,
            consumer_identity="research-agent", claim=claim,
            outcome=LOGICAL_RECEIVED, now=NOW + timedelta(seconds=2),
        )
    with pytest.raises(InternalFanoutError, match="SIGNAL_BINDING_INVALID"):
        read(fanout, "consume", report)
    assert read(fanout, "status", report) == "REVOKED_OR_INACTIVE"
