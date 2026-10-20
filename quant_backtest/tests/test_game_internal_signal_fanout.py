"""Offline adversarial coverage for internal two-team fanout."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from quant_backtest.quant_sector.internal_signal_fanout import (
    FAILED, LOGICAL_RECEIVED, RESEARCH, TRADING, InternalFanoutError,
    InternalSignalFanout,
)
from quant_backtest.quant_sector.upstream_inbox import QuantUpstreamReport, UpstreamInbox
from quant_backtest.watch_dispatch.signal_pool import SignalPoolEntry, STATUS_REVOKED

NOW = datetime(2026, 10, 20, 9, 30, tzinfo=timezone.utc)


def setup_fanout():
    inbox = UpstreamInbox()
    report = QuantUpstreamReport(
        report_id="report-1", report_kind="ANOMALY", event_id="evidence-event-1",
        evidence_hash="a" * 64, evidence_version=1,
        sender_agent_id="watch-agent", receiver_agent_id="quant-agent",
        created_at=NOW)
    receipt = inbox.accept(report, now=NOW)
    entries = {"signal-1": SignalPoolEntry(
        signal_id="signal-1", producer_agent_id="watch-agent",
        report_kind="ANOMALY", event_id="evidence-event-1",
        evidence_hash="a" * 64, evidence_version=1, created_at=NOW)}
    permissions = {(RESEARCH, "research-agent"), (TRADING, "trader-agent")}
    fanout = InternalSignalFanout(
        acceptance_lookup=inbox.get,
        acceptance_is_active=inbox.is_active,
        signal_lookup=lambda signal_id, now: entries.get(signal_id),
        consumer_authorized=lambda team, identity, now: (team, identity) in permissions)
    return inbox, report, receipt, permissions, entries, fanout


def plan(fanout, report, receipt, team=RESEARCH, identity="research-agent"):
    return fanout.plan(report=report, receipt_id=receipt.receipt_id,
                       signal_id="signal-1", team=team,
                       consumer_identity=identity, now=NOW)


def claim(fanout, team=RESEARCH, identity="research-agent"):
    return fanout.claim(signal_id="signal-1", team=team,
                        consumer_identity=identity, now=NOW + timedelta(seconds=1),
                        lease_until=NOW + timedelta(seconds=20))


def finish(fanout, ticket, team=RESEARCH, identity="research-agent",
           outcome=LOGICAL_RECEIVED, reply=None):
    return fanout.finish(signal_id="signal-1", team=team,
                         consumer_identity=identity, claim=ticket,
                         outcome=outcome, now=NOW + timedelta(seconds=2), reply=reply)


def test_requires_independent_trusted_ports_and_rejects_unaccepted_receipt():
    with pytest.raises(InternalFanoutError, match="TRUST_PORT_REQUIRED"):
        InternalSignalFanout(acceptance_lookup=None,
                             acceptance_is_active=lambda _: True,
                             signal_lookup=lambda *_: None,
                             consumer_authorized=lambda *_: True)
    inbox, report, receipt, permissions, entries, fanout = setup_fanout()
    with pytest.raises(InternalFanoutError, match="UPSTREAM_NOT_ACTIVE"):
        fanout.plan(report=report, receipt_id="not-a-real-receipt",
                    signal_id="signal-1", team=RESEARCH,
                    consumer_identity="research-agent", now=NOW)
    altered = QuantUpstreamReport(
        report_id=report.report_id, report_kind=report.report_kind,
        event_id=report.event_id, evidence_hash="b" * 64,
        evidence_version=1, sender_agent_id=report.sender_agent_id,
        receiver_agent_id=report.receiver_agent_id, created_at=NOW)
    with pytest.raises(InternalFanoutError, match="UPSTREAM_NOT_ACTIVE"):
        plan(fanout, altered, receipt)


def test_fanout_independent_branches_idempotent_and_conflicts():
    inbox, report, receipt, permissions, entries, fanout = setup_fanout()
    result = fanout.fanout(
        report=report, receipt_id=receipt.receipt_id,
        signal_id="signal-1", research_identity="research-agent",
        trading_identity="trader-agent", now=NOW)
    assert result.errors == ()
    assert result.research.team == RESEARCH
    assert result.research.route == "RESEARCH_REVIEW"
    assert result.trading.team == TRADING
    assert result.trading.route == "EXCHANGE_OBSERVATION"
    assert plan(fanout, report, receipt) is result.research
    with pytest.raises(InternalFanoutError, match="CONSUMER_NOT_AUTHORIZED"):
        plan(fanout, report, receipt, identity="attacker")
    permissions.add((RESEARCH, "other-agent"))
    with pytest.raises(InternalFanoutError, match="SUBSCRIPTION_CONFLICT"):
        plan(fanout, report, receipt, identity="other-agent")
    assert fanout.consume(signal_id="signal-1", team=TRADING,
                          consumer_identity="trader-agent", now=NOW) is None


def test_partial_failure_keeps_other_route_and_prevents_cross_route_reply():
    inbox, report, receipt, permissions, entries, fanout = setup_fanout()
    result = fanout.fanout(
        report=report, receipt_id=receipt.receipt_id, signal_id="signal-1",
        research_identity="missing-agent", trading_identity="trader-agent",
        now=NOW)
    assert result.research is None
    assert result.trading is not None
    assert result.errors == ((RESEARCH, "CONSUMER_NOT_AUTHORIZED"),)
    ticket = claim(fanout, TRADING, "trader-agent")
    with pytest.raises(InternalFanoutError, match="REPLY_INVALID"):
        finish(fanout, ticket, TRADING, "trader-agent", reply="RESEARCH_INTENT")
    response = finish(fanout, ticket, TRADING, "trader-agent", reply="TRADER_OBSERVE")
    assert response.reply == "TRADER_OBSERVE"
    assert response.outcome == LOGICAL_RECEIVED
    assert not hasattr(response, "external_message_id")
    assert not hasattr(response, "order_id")
    assert not hasattr(inbox, "dispatch")


def test_consumer_identity_permission_and_live_acceptance_rechecked():
    inbox, report, receipt, permissions, entries, fanout = setup_fanout()
    plan(fanout, report, receipt)
    with pytest.raises(InternalFanoutError, match="CONSUMER_NOT_AUTHORIZED"):
        claim(fanout, identity="attacker")
    ticket = claim(fanout)
    permissions.remove((RESEARCH, "research-agent"))
    with pytest.raises(InternalFanoutError, match="CONSUMER_NOT_AUTHORIZED"):
        finish(fanout, ticket)
    permissions.add((RESEARCH, "research-agent"))
    response = finish(fanout, ticket, reply="RESEARCH_INTENT")
    assert finish(fanout, ticket, reply="RESEARCH_INTENT") is response
    with pytest.raises(InternalFanoutError, match="RESPONSE_CONFLICT"):
        finish(fanout, ticket, outcome=FAILED)
    permissions.remove((RESEARCH, "research-agent"))
    with pytest.raises(InternalFanoutError, match="CONSUMER_NOT_AUTHORIZED"):
        fanout.consume(signal_id="signal-1", team=RESEARCH,
                       consumer_identity="research-agent", now=NOW + timedelta(seconds=3))


def test_revocation_blocks_claim_finish_consume_and_success_status():
    inbox, report, receipt, permissions, entries, fanout = setup_fanout()
    plan(fanout, report, receipt)
    ticket = claim(fanout)
    inbox.revoke(receipt.receipt_id, now=NOW + timedelta(seconds=1))
    for action in (
        lambda: plan(fanout, report, receipt),
        lambda: claim(fanout),
        lambda: finish(fanout, ticket),
        lambda: fanout.consume(signal_id="signal-1", team=RESEARCH,
                               consumer_identity="research-agent", now=NOW + timedelta(seconds=2)),
    ):
        with pytest.raises(InternalFanoutError, match="UPSTREAM_NOT_ACTIVE"):
            action()
    assert fanout.status(signal_id="signal-1", team=RESEARCH,
                         consumer_identity="research-agent",
                         now=NOW + timedelta(seconds=2)) == "REVOKED_OR_INACTIVE"


def test_revocation_after_logical_result_never_exposes_delivered_state():
    inbox, report, receipt, permissions, entries, fanout = setup_fanout()
    plan(fanout, report, receipt)
    ticket = claim(fanout)
    finish(fanout, ticket)
    inbox.revoke(receipt.receipt_id, now=NOW + timedelta(seconds=3))
    assert fanout.status(signal_id="signal-1", team=RESEARCH,
                         consumer_identity="research-agent",
                         now=NOW + timedelta(seconds=3)) == "REVOKED_OR_INACTIVE"
    with pytest.raises(InternalFanoutError, match="UPSTREAM_NOT_ACTIVE"):
        fanout.consume(signal_id="signal-1", team=RESEARCH,
                       consumer_identity="research-agent", now=NOW + timedelta(seconds=3))


def test_time_windows_and_no_out_of_lease_settlement():
    inbox, report, receipt, permissions, entries, fanout = setup_fanout()
    with pytest.raises(InternalFanoutError, match="ACCEPTANCE_TIME_INVALID"):
        fanout.plan(report=report, receipt_id=receipt.receipt_id,
                    signal_id="signal-1", team=RESEARCH,
                    consumer_identity="research-agent", now=NOW - timedelta(seconds=1))
    plan(fanout, report, receipt)
    with pytest.raises(InternalFanoutError, match="CLAIM_WINDOW_INVALID"):
        fanout.claim(signal_id="signal-1", team=RESEARCH,
                     consumer_identity="research-agent", now=NOW,
                     lease_until=NOW)
    ticket = claim(fanout)
    with pytest.raises(InternalFanoutError, match="CLAIM_EXPIRED"):
        fanout.finish(signal_id="signal-1", team=RESEARCH,
                      consumer_identity="research-agent", claim=ticket,
                      outcome=LOGICAL_RECEIVED,
                      now=ticket.lease_until)
    assert fanout.consume(signal_id="signal-1", team=RESEARCH,
                          consumer_identity="research-agent", now=NOW + timedelta(seconds=2)) is None


def test_cross_route_claim_and_forged_identity_cannot_settle():
    inbox, report, receipt, permissions, entries, fanout = setup_fanout()
    plan(fanout, report, receipt)
    plan(fanout, report, receipt, TRADING, "trader-agent")
    research_ticket = claim(fanout)
    trading_ticket = claim(fanout, TRADING, "trader-agent")
    with pytest.raises(InternalFanoutError, match="CLAIM_NOT_CURRENT"):
        finish(fanout, research_ticket, TRADING, "trader-agent")
    permissions.add((RESEARCH, "attacker"))
    with pytest.raises(InternalFanoutError, match="CONSUMER_MISMATCH"):
        finish(fanout, research_ticket, RESEARCH, "attacker")
    assert finish(fanout, trading_ticket, TRADING, "trader-agent",
                  outcome=FAILED).outcome == FAILED
    assert fanout.consume(signal_id="signal-1", team=RESEARCH,
                          consumer_identity="research-agent",
                          now=NOW + timedelta(seconds=2)) is None


def test_distinct_signal_event_binding_missing_mismatch_and_revocation():
    inbox, report, receipt, permissions, entries, fanout = setup_fanout()
    assert report.event_id != entries["signal-1"].signal_id
    with pytest.raises(InternalFanoutError, match="SIGNAL_BINDING_INVALID"):
        fanout.plan(report=report, receipt_id=receipt.receipt_id,
                    signal_id="evidence-event-1", team=RESEARCH,
                    consumer_identity="research-agent", now=NOW)
    entries["signal-1"] = replace(entries["signal-1"], event_id="wrong-event")
    with pytest.raises(InternalFanoutError, match="SIGNAL_BINDING_INVALID"):
        plan(fanout, report, receipt)
    entries["signal-1"] = replace(entries["signal-1"], event_id=report.event_id,
                                  producer_agent_id="forged-producer")
    with pytest.raises(InternalFanoutError, match="SIGNAL_BINDING_INVALID"):
        plan(fanout, report, receipt)
    entries["signal-1"] = replace(entries["signal-1"], producer_agent_id="watch-agent")
    plan(fanout, report, receipt)
    ticket = claim(fanout)
    entries["signal-1"] = replace(entries["signal-1"], status=STATUS_REVOKED, epoch=1)
    with pytest.raises(InternalFanoutError, match="SIGNAL_BINDING_INVALID"):
        finish(fanout, ticket)
    with pytest.raises(InternalFanoutError, match="SIGNAL_BINDING_INVALID"):
        fanout.consume(signal_id="signal-1", team=RESEARCH,
                       consumer_identity="research-agent", now=NOW + timedelta(seconds=2))
    assert fanout.status(signal_id="signal-1", team=RESEARCH,
                         consumer_identity="research-agent",
                         now=NOW + timedelta(seconds=2)) == "REVOKED_OR_INACTIVE"


def test_health_is_not_a_business_subscription():
    inbox, report, receipt, permissions, entries, fanout = setup_fanout()
    health = replace(report, report_id="health-report", report_kind="HEALTH")
    health_receipt = inbox.accept(health, now=NOW)
    entries["signal-1"] = replace(entries["signal-1"], report_kind="HEALTH")
    with pytest.raises(InternalFanoutError, match="BUSINESS_SIGNAL_REQUIRED"):
        plan(fanout, health, health_receipt)
    result = fanout.fanout(report=health, receipt_id=health_receipt.receipt_id,
                           signal_id="signal-1", research_identity="research-agent",
                           trading_identity="trader-agent", now=NOW)
    assert result.research is None and result.trading is None
    assert result.errors == ((RESEARCH, "BUSINESS_SIGNAL_REQUIRED"),
                             (TRADING, "BUSINESS_SIGNAL_REQUIRED"))


def test_asof_projection_hides_future_subscription_claim_and_response():
    inbox, report, receipt, permissions, entries, fanout = setup_fanout()
    future = NOW + timedelta(seconds=10)
    early = NOW + timedelta(seconds=1)
    subscription = fanout.plan(report=report, receipt_id=receipt.receipt_id,
                               signal_id="signal-1", team=RESEARCH,
                               consumer_identity="research-agent", now=future)
    for operation in (
        lambda: fanout.status(signal_id="signal-1", team=RESEARCH,
                              consumer_identity="research-agent", now=early),
        lambda: fanout.consume(signal_id="signal-1", team=RESEARCH,
                               consumer_identity="research-agent", now=early),
        lambda: fanout.plan(report=report, receipt_id=receipt.receipt_id,
                            signal_id="signal-1", team=RESEARCH,
                            consumer_identity="research-agent", now=early),
    ):
        with pytest.raises(InternalFanoutError, match="SUBSCRIPTION_NOT_VISIBLE"):
            operation()
    assert fanout.status(signal_id="signal-1", team=RESEARCH,
                         consumer_identity="research-agent", now=future) == "PLANNED"
    ticket = fanout.claim(signal_id="signal-1", team=RESEARCH,
                          consumer_identity="research-agent", now=future + timedelta(seconds=1),
                          lease_until=future + timedelta(seconds=20))
    assert fanout.status(signal_id="signal-1", team=RESEARCH,
                         consumer_identity="research-agent", now=future) == "PLANNED"
    response = fanout.finish(signal_id="signal-1", team=RESEARCH,
                             consumer_identity="research-agent", claim=ticket,
                             outcome=LOGICAL_RECEIVED,
                             now=future + timedelta(seconds=2))
    assert fanout.status(signal_id="signal-1", team=RESEARCH,
                         consumer_identity="research-agent", now=future + timedelta(seconds=1)) == "CLAIMED"
    assert fanout.consume(signal_id="signal-1", team=RESEARCH,
                          consumer_identity="research-agent",
                          now=future + timedelta(seconds=1)) is None
    assert fanout.consume(signal_id="signal-1", team=RESEARCH,
                          consumer_identity="research-agent",
                          now=response.recorded_at) is response
    assert subscription.signal_id != report.event_id


def test_identical_finish_before_recorded_at_cannot_return_future_result():
    inbox, report, receipt, permissions, entries, fanout = setup_fanout()
    plan(fanout, report, receipt)
    ticket = claim(fanout)
    future = NOW + timedelta(seconds=10)
    response = fanout.finish(signal_id="signal-1", team=RESEARCH,
                             consumer_identity="research-agent", claim=ticket,
                             outcome=LOGICAL_RECEIVED, now=future)
    with pytest.raises(InternalFanoutError, match="RESPONSE_NOT_VISIBLE"):
        fanout.finish(signal_id="signal-1", team=RESEARCH,
                      consumer_identity="research-agent", claim=ticket,
                      outcome=LOGICAL_RECEIVED, now=NOW + timedelta(seconds=2))
    assert fanout.finish(signal_id="signal-1", team=RESEARCH,
                         consumer_identity="research-agent", claim=ticket,
                         outcome=LOGICAL_RECEIVED, now=future) is response


def test_callback_errors_fail_closed_without_other_side_effects():
    inbox, report, receipt, permissions, entries, fanout = setup_fanout()
    def exploding(*args):
        raise RuntimeError("unavailable")
    broken = InternalSignalFanout(
        acceptance_lookup=inbox.get, acceptance_is_active=exploding,
        signal_lookup=lambda signal_id, now: entries.get(signal_id),
        consumer_authorized=lambda team, identity, now: True)
    with pytest.raises(InternalFanoutError, match="UPSTREAM_NOT_ACTIVE"):
        plan(broken, report, receipt)
    assert broken.fanout(report=report, receipt_id=receipt.receipt_id,
                         signal_id="signal-1", research_identity="research-agent",
                         trading_identity="trader-agent", now=NOW).errors == (
                             (RESEARCH, "UPSTREAM_NOT_ACTIVE"),
                             (TRADING, "UPSTREAM_NOT_ACTIVE"))
    with pytest.raises(InternalFanoutError, match="SUBSCRIPTION_UNKNOWN"):
        broken.status(signal_id="signal-1", team=RESEARCH,
                      consumer_identity="research-agent", now=NOW)
    assert inbox.ledger_epoch == 0
    assert inbox.is_active(receipt.receipt_id)
