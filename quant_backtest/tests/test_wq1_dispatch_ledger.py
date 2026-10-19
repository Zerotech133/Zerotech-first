"""W-Q1 three-route dispatch ledger tests: lease, UNKNOWN, bounded retry.

Offline only; no network, no persistence, no execution semantics.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from quant_backtest.quant_sector.dispatch_ledger import (
    ACCEPTED_VERIFIED,
    CANCELLED,
    CLAIMED,
    DEFINITELY_REJECTED,
    DispatchIntent,
    DispatchLedger,
    DispatchLedgerError,
    READY,
    UNKNOWN,
)
from quant_backtest.quant_sector.upstream_inbox import (
    QuantUpstreamReport,
    UpstreamInbox,
    UpstreamReceipt,
)

T = datetime(2026, 10, 19, 2, 0, tzinfo=timezone.utc)
H = "a" * 64
ROUTES = ("FEISHU_INFO", "RESEARCH_REVIEW", "EXCHANGE_OBSERVATION")
ROUTES_BAD = ("QUANT_UPSTREAM_REPORT", "SIM_DISPATCH", "feishu_info", "")


def make_report(**changes) -> QuantUpstreamReport:
    fields = dict(report_id="rep1", report_kind="ANOMALY", event_id="ev1",
                  evidence_hash=H, evidence_version=1,
                  sender_agent_id="watch-agent-1",
                  receiver_agent_id="quant-sector-agent",
                  created_at=T)
    fields.update(changes)
    return QuantUpstreamReport(**fields)


def fixture_inbox() -> tuple:
    inbox = UpstreamInbox()
    report = make_report()
    receipt = inbox.accept(report, now=T)
    return inbox, report, receipt


def make_ledger(route: str, inbox: UpstreamInbox) -> DispatchLedger:
    return DispatchLedger(route, inbox_lookup=inbox.is_active)


def make_intent(route: str, receipt: UpstreamReceipt, *,
                intent_id: str = "int1", target_ref: str = "feishu:chan1",
                created_at: datetime = T) -> DispatchIntent:
    return DispatchIntent(intent_id=intent_id, receipt_id=receipt.receipt_id,
                          route=route, target_ref=target_ref,
                          created_at=created_at)


def plan(ledger: DispatchLedger, inbox_receipt: UpstreamReceipt, **kwargs):
    intent = kwargs.pop("intent") if "intent" in kwargs else make_intent(
        ledger.route, inbox_receipt, **kwargs)
    return ledger.plan(intent, receipt_view=inbox_receipt, now=T), intent


def test_route_enum_is_exactly_three_values_and_fail_closed():
    assert set(ROUTES) == {"FEISHU_INFO", "RESEARCH_REVIEW",
                           "EXCHANGE_OBSERVATION"}
    inbox, _, _ = fixture_inbox()
    for route in ROUTES:
        assert make_ledger(route, inbox).route == route
    for route in ROUTES_BAD:
        with pytest.raises(DispatchLedgerError, match="ROUTE_INVALID"):
            DispatchLedger(route, inbox_lookup=inbox.is_active)
        with pytest.raises(DispatchLedgerError, match="ROUTE_INVALID"):
            make_intent(route, inbox.list_accepted()[0])


def test_plan_requires_accepted_active_receipt():
    inbox, _, receipt = fixture_inbox()
    ledger = make_ledger("FEISHU_INFO", inbox)
    # No inbox lookup injected: fail closed even with a valid receipt view.
    naked = DispatchLedger("FEISHU_INFO")
    with pytest.raises(DispatchLedgerError, match="UPSTREAM_NOT_ACCEPTED"):
        naked.plan(make_intent("FEISHU_INFO", receipt), receipt_view=receipt,
                   now=T)
    # Receipt reference mismatch: not the accepted receipt of this event.
    other_inbox = UpstreamInbox()
    other_inbox.accept(make_report(report_id="repX"), now=T)
    other_receipt = other_inbox.receipt_for_report("repX")
    with pytest.raises(DispatchLedgerError, match="UPSTREAM_NOT_ACCEPTED"):
        ledger.plan(make_intent("FEISHU_INFO", receipt, intent_id="intX"),
                    receipt_view=other_receipt, now=T)
    # Revoked upstream acceptance blocks planning.
    inbox.revoke(receipt.receipt_id, now=T + timedelta(seconds=1))
    with pytest.raises(DispatchLedgerError, match="UPSTREAM_NOT_ACCEPTED"):
        ledger.plan(make_intent("FEISHU_INFO", receipt), receipt_view=receipt,
                    now=T + timedelta(seconds=2))


def test_plan_idempotent_and_conflict_closed():
    inbox, _, receipt = fixture_inbox()
    ledger = make_ledger("FEISHU_INFO", inbox)
    planned, intent = plan(ledger, receipt)
    assert ledger.status(intent.intent_id) == READY
    again, _ = plan(ledger, receipt)
    assert again is planned
    with pytest.raises(DispatchLedgerError, match="INTENT_CONFLICT"):
        plan(ledger, receipt, intent_id="int1", target_ref="feishu:chan2")
    with pytest.raises(DispatchLedgerError, match="ROUTE_MISMATCH"):
        ledger.plan(make_intent("RESEARCH_REVIEW", receipt),
                    receipt_view=receipt, now=T)


def test_naive_time_rejected_in_plan_and_claim():
    naive = datetime(2026, 10, 19, 2, 0)
    inbox, _, receipt = fixture_inbox()
    ledger = make_ledger("FEISHU_INFO", inbox)
    with pytest.raises(DispatchLedgerError, match="PLAN_NOW_INVALID"):
        ledger.plan(make_intent("FEISHU_INFO", receipt), receipt_view=receipt,
                    now=naive)
    planned, intent = plan(ledger, receipt)
    with pytest.raises(DispatchLedgerError, match="CLAIM_NOW_INVALID"):
        ledger.claim(intent.intent_id, now=naive,
                     lease_until=T + timedelta(minutes=1))
    with pytest.raises(DispatchLedgerError, match="LEASE_UNTIL_INVALID"):
        ledger.claim(intent.intent_id, now=T, lease_until=naive)


def test_three_routes_fully_independent():
    inbox, _, receipt = fixture_inbox()
    ledgers = {route: make_ledger(route, inbox) for route in ROUTES}
    intents = {}
    for route, ledger in ledgers.items():
        _, intent = plan(ledger, receipt, intent_id=f"int-{route}")
        intents[route] = intent
    # One route ends UNKNOWN; the other two still complete independently.
    a = ledgers["FEISHU_INFO"].claim(intents["FEISHU_INFO"].intent_id,
                                     now=T, lease_until=T + timedelta(minutes=5))
    finished = ledgers["FEISHU_INFO"].finish(a, UNKNOWN, now=T + timedelta(seconds=10))
    assert finished.outcome == UNKNOWN
    assert ledgers["FEISHU_INFO"].status(intents["FEISHU_INFO"].intent_id) == UNKNOWN
    for route in ("RESEARCH_REVIEW", "EXCHANGE_OBSERVATION"):
        attempt = ledgers[route].claim(intents[route].intent_id, now=T,
                                       lease_until=T + timedelta(minutes=5))
        done = ledgers[route].finish(
            attempt, ACCEPTED_VERIFIED, now=T + timedelta(seconds=10),
            external_message_id=f"msg-{route}", observed_at=T + timedelta(seconds=5))
        assert done.outcome == ACCEPTED_VERIFIED
        assert done.external_message_id == f"msg-{route}"
        assert ledgers[route].status(intents[route].intent_id) == ACCEPTED_VERIFIED


def test_claim_lease_windows_and_sequence():
    inbox, _, receipt = fixture_inbox()
    ledger = make_ledger("FEISHU_INFO", inbox)
    _, intent = plan(ledger, receipt)
    # Lease must end strictly after the claim moment.
    with pytest.raises(DispatchLedgerError, match="LEASE_WINDOW_INVALID"):
        ledger.claim(intent.intent_id, now=T, lease_until=T)
    # Claim before the intent exists in time is rejected.
    with pytest.raises(DispatchLedgerError, match="CLAIM_WINDOW_INVALID"):
        ledger.claim(intent.intent_id, now=T - timedelta(seconds=1),
                     lease_until=T + timedelta(minutes=1))
    first = ledger.claim(intent.intent_id, now=T,
                         lease_until=T + timedelta(minutes=5))
    assert first.attempt_no == 1 and first.outcome == CLAIMED
    with pytest.raises(DispatchLedgerError, match="INTENT_NOT_CLAIMABLE"):
        ledger.claim(intent.intent_id, now=T + timedelta(seconds=1),
                     lease_until=T + timedelta(minutes=5))
    with pytest.raises(DispatchLedgerError, match="INTENT_UNKNOWN"):
        ledger.claim("missing-intent", now=T,
                     lease_until=T + timedelta(minutes=5))


def test_unknown_is_terminal_without_auto_retry():
    inbox, _, receipt = fixture_inbox()
    ledger = make_ledger("FEISHU_INFO", inbox)
    _, intent = plan(ledger, receipt)
    attempt = ledger.claim(intent.intent_id, now=T,
                           lease_until=T + timedelta(minutes=5))
    finished = ledger.finish(attempt, UNKNOWN, now=T + timedelta(seconds=10))
    assert finished.outcome == UNKNOWN
    assert ledger.status(intent.intent_id) == UNKNOWN
    with pytest.raises(DispatchLedgerError, match="INTENT_NOT_CLAIMABLE"):
        ledger.claim(intent.intent_id, now=T + timedelta(seconds=20),
                     lease_until=T + timedelta(minutes=5))
    # Re-plan stays idempotent and does NOT resurrect a terminal intent.
    replanned = ledger.plan(intent, receipt_view=receipt,
                            now=T + timedelta(seconds=20))
    assert replanned == intent
    with pytest.raises(DispatchLedgerError, match="INTENT_NOT_CLAIMABLE"):
        ledger.claim(intent.intent_id, now=T + timedelta(seconds=20),
                     lease_until=T + timedelta(minutes=5))
    with pytest.raises(DispatchLedgerError, match="ATTEMPT_NOT_CURRENT"):
        ledger.finish(attempt, ACCEPTED_VERIFIED, now=T + timedelta(seconds=20))


def test_finish_evidence_mismatch_forces_unknown():
    inbox, _, receipt = fixture_inbox()
    ledger = make_ledger("RESEARCH_REVIEW", inbox)
    _, intent = plan(ledger, receipt, intent_id="int-ev",
                     target_ref="research:desk2")
    attempt = ledger.claim(intent.intent_id, now=T,
                           lease_until=T + timedelta(minutes=5))
    # Missing external_message_id.
    forced = ledger.finish(attempt, ACCEPTED_VERIFIED,
                           now=T + timedelta(seconds=10),
                           observed_at=T + timedelta(seconds=5))
    assert forced.outcome == UNKNOWN
    assert forced.external_message_id is None and forced.observed_at is None
    # observed_at outside the claim window.
    ledger2 = make_ledger("RESEARCH_REVIEW", inbox)
    _, intent2 = plan(ledger2, receipt, intent_id="int-ev2",
                      target_ref="research:desk2")
    attempt2 = ledger2.claim(intent2.intent_id, now=T,
                             lease_until=T + timedelta(minutes=5))
    forced2 = ledger2.finish(attempt2, DEFINITELY_REJECTED,
                             now=T + timedelta(seconds=10),
                             external_message_id="msg-1",
                             observed_at=T - timedelta(seconds=1))
    assert forced2.outcome == UNKNOWN
    # Lease already expired: verified outcome degrades to UNKNOWN.
    ledger3 = make_ledger("RESEARCH_REVIEW", inbox)
    _, intent3 = plan(ledger3, receipt, intent_id="int-ev3",
                      target_ref="research:desk2")
    attempt3 = ledger3.claim(intent3.intent_id, now=T,
                             lease_until=T + timedelta(minutes=1))
    forced3 = ledger3.finish(attempt3, ACCEPTED_VERIFIED,
                             now=T + timedelta(minutes=2),
                             external_message_id="msg-2",
                             observed_at=T + timedelta(seconds=5))
    assert forced3.outcome == UNKNOWN
    assert ledger3.status(intent3.intent_id) == UNKNOWN
    with pytest.raises(DispatchLedgerError, match="ATTEMPT_NOT_CURRENT"):
        ledger3.finish(forced3, "SOME_OTHER", now=T + timedelta(minutes=3))
    # An invalid outcome on a live claimed attempt fails closed.
    ledger4 = make_ledger("RESEARCH_REVIEW", inbox)
    _, intent4 = plan(ledger4, receipt, intent_id="int-ev4",
                      target_ref="research:desk2")
    attempt4 = ledger4.claim(intent4.intent_id, now=T,
                             lease_until=T + timedelta(minutes=1))
    with pytest.raises(DispatchLedgerError, match="OUTCOME_INVALID"):
        ledger4.finish(attempt4, "SOME_OTHER", now=T + timedelta(seconds=5))


def test_definitely_rejected_allows_controlled_retry():
    inbox, _, receipt = fixture_inbox()
    ledger = make_ledger("EXCHANGE_OBSERVATION", inbox)
    _, intent = plan(ledger, receipt, intent_id="int-obs",
                     target_ref="desk:trader1")
    first = ledger.claim(intent.intent_id, now=T,
                         lease_until=T + timedelta(minutes=5))
    rejected = ledger.finish(first, DEFINITELY_REJECTED,
                             now=T + timedelta(seconds=10),
                             external_message_id="rej-1",
                             observed_at=T + timedelta(seconds=5))
    assert rejected.outcome == DEFINITELY_REJECTED
    assert ledger.status(intent.intent_id) == READY
    # Controlled retry under the same planned intent.
    second = ledger.claim(intent.intent_id, now=T + timedelta(seconds=20),
                          lease_until=T + timedelta(minutes=6))
    assert second.attempt_no == 2


def test_attempts_capped_at_three():
    inbox, _, receipt = fixture_inbox()
    ledger = make_ledger("FEISHU_INFO", inbox)
    _, intent = plan(ledger, receipt, intent_id="int-cap")
    for no in (1, 2, 3):
        attempt = ledger.claim(intent.intent_id, now=T,
                               lease_until=T + timedelta(minutes=5))
        assert attempt.attempt_no == no
        ledger.finish(attempt, DEFINITELY_REJECTED, now=T + timedelta(seconds=1),
                      external_message_id=f"rej-{no}",
                      observed_at=T + timedelta(seconds=1))
    with pytest.raises(DispatchLedgerError, match="ATTEMPTS_EXHAUSTED"):
        ledger.claim(intent.intent_id, now=T + timedelta(seconds=2),
                     lease_until=T + timedelta(minutes=5))


def test_cancel_and_revoke_epoch_only_forward():
    inbox, _, receipt = fixture_inbox()
    ledger = make_ledger("FEISHU_INFO", inbox)
    _, ready_intent = plan(ledger, receipt, intent_id="int-cancel")
    assert ledger.cancel(ready_intent.intent_id, now=T + timedelta(seconds=1)) is True
    assert ledger.status(ready_intent.intent_id) == CANCELLED
    assert ledger.cancel(ready_intent.intent_id, now=T + timedelta(seconds=2)) is False
    with pytest.raises(DispatchLedgerError, match="INTENT_NOT_CLAIMABLE"):
        ledger.claim(ready_intent.intent_id, now=T + timedelta(seconds=3),
                     lease_until=T + timedelta(minutes=5))
    # Revoke a claimed intent: forced terminal UNKNOWN, evidence dropped.
    _, claimed_intent = plan(ledger, receipt, intent_id="int-revoke")
    attempt = ledger.claim(claimed_intent.intent_id, now=T + timedelta(seconds=4),
                           lease_until=T + timedelta(minutes=5))
    epoch = ledger.revoke(claimed_intent.intent_id,
                          now=T + timedelta(seconds=5))
    assert epoch == 1 and ledger.ledger_epoch == 1
    assert ledger.status(claimed_intent.intent_id) == UNKNOWN
    assert ledger.get_attempts(claimed_intent.intent_id)[-1].outcome == UNKNOWN
    with pytest.raises(DispatchLedgerError, match="INTENT_REVOKED"):
        ledger.plan(claimed_intent, receipt_view=receipt,
                    now=T + timedelta(seconds=6))
    with pytest.raises(DispatchLedgerError, match="INTENT_UNKNOWN"):
        ledger.claim(claimed_intent.intent_id, now=T + timedelta(seconds=6),
                     lease_until=T + timedelta(minutes=5))
    # Epoch rollback is rejected; forward jumps are accepted.
    with pytest.raises(DispatchLedgerError, match="EPOCH_NOT_ADVANCED"):
        ledger.revoke(claimed_intent.intent_id, now=T + timedelta(seconds=7),
                      epoch=1)
    with pytest.raises(DispatchLedgerError, match="EPOCH_NOT_ADVANCED"):
        ledger.revoke(claimed_intent.intent_id, now=T + timedelta(seconds=7),
                      epoch=0)
    assert ledger.ledger_epoch == 1
    assert ledger.revoke(claimed_intent.intent_id,
                         now=T + timedelta(seconds=8), epoch=5) == 5
    # No object carries any trading permission concept.
    for obj in (ledger, attempt):
        assert not hasattr(obj, "trading_permitted")
