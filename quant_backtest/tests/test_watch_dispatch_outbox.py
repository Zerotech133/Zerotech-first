"""G1 offline evidence store and three-route outbox tests. No external effects."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from quant_backtest.watch_dispatch import EvidenceEnvelope, RouteIntent, freeze_offline
from quant_backtest.watch_dispatch.evidence_store import EvidenceConflict, EvidenceStore
from quant_backtest.watch_dispatch.route_outbox import (
    ACCEPTED_VERIFIED, CLAIMED, CANCELLED, DEFINITELY_REJECTED, OutboxError,
    READY_OFFLINE, RouteOutbox, UNKNOWN,
)

T = datetime(2026, 10, 19, 2, 0, tzinfo=timezone.utc)
H = "a" * 64


def sample(**changes):
    fields = dict(schema_version=1, event_id="ev1", evidence_version=1,
                  previous_version_hash=None, pool="B", tenant_or_owner_id="owner1",
                  market="SH", code="600000", source_membership_id="member1",
                  source_revision="rev1", rule_id="r1", rule_version=1,
                  comparator="GE", threshold="1.00", unit="ratio", window="5m",
                  observed_value="1.10", baseline_value="1.00", raw_ref="raw:1",
                  raw_digest=H, source_id="src1", calendar_id="cal1",
                  source_as_of=T, visible_at=T, detected_at=T, freeze_at=T,
                  valid_until=T + timedelta(minutes=30), facts=("observed",),
                  hypotheses=("candidate",), supporting_refs=("raw:1",),
                  contradicting_refs=(), confirmation_condition="confirmed later",
                  invalidation_condition="source revoked")
    fields.update(changes)
    return freeze_offline(**fields)


def plan(outbox, envelope, *, route="QUANT_UPSTREAM_REPORT", target_id="desk1",
         intent_id="intent1", created_at=None, valid_until=None, **changes):
    return outbox.plan(
        envelope, route=route, target_type="desk", target_id=target_id,
        policy_version="p1", control_epoch=changes.pop("control_epoch", 0),
        intent_id=intent_id,
        created_at=created_at or envelope.freeze_at,
        valid_until=valid_until or envelope.valid_until - timedelta(minutes=1),
        **changes)


def claim(outbox, envelope, *, route="QUANT_UPSTREAM_REPORT", target_id="desk1",
          token="tok1", claim_at=None, lease_until=None):
    return outbox.claim(
        envelope, route=route, target_type="desk", target_id=target_id,
        claim_token=token, claim_at=claim_at or envelope.freeze_at,
        lease_until=lease_until or envelope.valid_until - timedelta(minutes=5))


def test_store_freeze_idempotent_and_conflict_closed():
    store = EvidenceStore()
    envelope = sample()
    assert store.freeze(envelope) == envelope
    assert store.freeze(envelope) == envelope
    assert store.get_exact(envelope) == envelope
    assert store.latest(envelope) == envelope
    twin = sample(observed_value="1.20")
    with pytest.raises(EvidenceConflict, match="EVIDENCE_CONFLICT"):
        store.freeze(twin)
    assert store.production_permitted is False
    assert store.notification_permitted is False
    assert store.trading_permitted is False


def test_store_requires_frozen_previous_for_chained_version():
    first = sample()
    second = sample(evidence_version=2, _previous=first,
                    freeze_at=T + timedelta(minutes=1))
    orphan_store = EvidenceStore()
    with pytest.raises(EvidenceConflict, match="PREVIOUS_NOT_FROZEN"):
        orphan_store.freeze(second)
    store = EvidenceStore()
    store.freeze(first)
    store.freeze(second)
    assert store.latest(first).evidence_version == 2


def test_outbox_plan_idempotent_conflict_and_time_bounds():
    outbox = RouteOutbox()
    envelope = sample()
    intent = plan(outbox, envelope)
    assert intent.status == READY_OFFLINE
    assert plan(outbox, envelope) == intent
    with pytest.raises(OutboxError, match="INTENT_CONFLICT"):
        plan(outbox, envelope, intent_id="other")
    with pytest.raises(OutboxError, match="INTENT_CONFLICT"):
        plan(outbox, envelope, valid_until=envelope.valid_until)
    with pytest.raises(OutboxError, match="ROUTE_TIME_INVALID"):
        plan(outbox, envelope,
             valid_until=envelope.valid_until + timedelta(minutes=1))
    with pytest.raises(ValueError, match="NOT_CURRENT"):
        plan(outbox, envelope, created_at=envelope.valid_until)
    with pytest.raises(OutboxError, match="ROUTE_INVALID"):
        plan(outbox, envelope, route="SIM_DISPATCH")


def test_claim_finish_accepted_and_terminal():
    outbox = RouteOutbox()
    envelope = sample()
    plan(outbox, envelope)
    attempt = claim(outbox, envelope)
    assert attempt.attempt_no == 1 and attempt.outcome == CLAIMED
    finished = outbox.finish(
        attempt.attempt_id, outcome=ACCEPTED_VERIFIED, now=T + timedelta(seconds=30),
        external_message_id="msg-1", verification_source="provider:feishu",
        observed_at=T + timedelta(seconds=10))
    assert finished.outcome == ACCEPTED_VERIFIED
    assert finished.receipt is not None and finished.receipt.observed_at == T + timedelta(seconds=10)
    assert outbox.get_intent(envelope, route="QUANT_UPSTREAM_REPORT",
                             target_type="desk", target_id="desk1").status == ACCEPTED_VERIFIED
    with pytest.raises(OutboxError, match="INTENT_NOT_CLAIMABLE"):
        claim(outbox, envelope)
    for item in (attempt, finished):
        assert item.production_permitted is False
        assert item.notification_permitted is False
        assert item.trading_permitted is False


@pytest.mark.parametrize("bad_receipt", [
    {"observed_at": T - timedelta(seconds=1)},
    {"observed_at": None}, {"external_message_id": None},
    {"external_message_id": ""},
])
def test_receipt_outside_claim_window_forces_unknown(bad_receipt):
    outbox = RouteOutbox()
    envelope = sample()
    plan(outbox, envelope)
    attempt = claim(outbox, envelope)
    kwargs = dict(outcome=ACCEPTED_VERIFIED, now=T + timedelta(seconds=30),
                  external_message_id="msg-1", verification_source="provider",
                  observed_at=T + timedelta(seconds=10))
    kwargs.update(bad_receipt)
    finished = outbox.finish(attempt.attempt_id, **kwargs)
    assert finished.outcome == UNKNOWN and finished.receipt is None
    assert outbox.get_intent(envelope, route="QUANT_UPSTREAM_REPORT",
                             target_type="desk", target_id="desk1").status == UNKNOWN
    with pytest.raises(OutboxError, match="INTENT_NOT_CLAIMABLE"):
        claim(outbox, envelope)


def test_lease_expiry_and_stale_finish_forced_unknown():
    outbox = RouteOutbox()
    envelope = sample()
    plan(outbox, envelope)
    attempt = claim(outbox, envelope, lease_until=T + timedelta(minutes=5))
    finished = outbox.finish(
        attempt.attempt_id, outcome=ACCEPTED_VERIFIED,
        now=attempt.lease_until, external_message_id="msg-1",
        verification_source="provider", observed_at=T + timedelta(minutes=4))
    assert finished.outcome == UNKNOWN


def test_rejection_allows_bounded_retry_then_exhausted():
    outbox = RouteOutbox()
    envelope = sample()
    plan(outbox, envelope)
    for no in (1, 2, 3):
        attempt = claim(outbox, envelope, token=f"tok-{no}")
        assert attempt.attempt_no == no
        finished = outbox.finish(
            attempt.attempt_id, outcome=DEFINITELY_REJECTED,
            now=T + timedelta(seconds=no * 10), external_message_id=f"rej-{no}",
            verification_source="provider", observed_at=T + timedelta(seconds=no * 5))
        assert finished.outcome == DEFINITELY_REJECTED
        status = outbox.get_intent(envelope, route="QUANT_UPSTREAM_REPORT",
                                   target_type="desk", target_id="desk1").status
        assert status == (READY_OFFLINE if no < 3 else READY_OFFLINE)
    with pytest.raises(OutboxError, match="ATTEMPTS_EXHAUSTED"):
        claim(outbox, envelope, token="tok-4")


def test_cancel_and_revoke_fail_closed():
    outbox = RouteOutbox()
    envelope = sample()
    plan(outbox, envelope, target_id="quant-desk-1", intent_id="intent-a")
    assert outbox.cancel(envelope, route="QUANT_UPSTREAM_REPORT", target_type="desk",
                         target_id="quant-desk-1", now=T) is True
    assert outbox.get_intent(envelope, route="QUANT_UPSTREAM_REPORT", target_type="desk",
                             target_id="quant-desk-1").status == CANCELLED
    plan(outbox, envelope, target_id="quant-desk-2", intent_id="intent-r")
    attempt = claim(outbox, envelope, target_id="quant-desk-2", token="tok-r")
    outbox.revoke_envelope(envelope, epoch=1, now=T + timedelta(seconds=5))
    revoked = outbox.get_intent(envelope, route="QUANT_UPSTREAM_REPORT",
                                target_type="desk", target_id="quant-desk-2")
    assert revoked.status == UNKNOWN
    with pytest.raises(OutboxError, match="ATTEMPT_NOT_CURRENT"):
        outbox.finish(attempt.attempt_id, outcome=ACCEPTED_VERIFIED,
                      now=T + timedelta(seconds=6), external_message_id="m",
                      verification_source="s", observed_at=T + timedelta(seconds=6))
    with pytest.raises(OutboxError, match="EPOCH_STALE"):
        plan(outbox, envelope, target_id="quant-desk-3")
    with pytest.raises(OutboxError, match="EPOCH_NOT_ADVANCED"):
        outbox.revoke_envelope(envelope, epoch=1, now=T + timedelta(seconds=7))


def test_per_envelope_attempt_audit_trail_isolated():
    outbox = RouteOutbox()
    envelope = sample()
    other = sample(event_id="ev2")
    plan(outbox, envelope, target_id="quant-desk-1", intent_id="i-1")
    plan(outbox, other, target_id="quant-desk-1", intent_id="i-2")
    attempt = claim(outbox, envelope, target_id="quant-desk-1",
                    claim_at=T + timedelta(seconds=1))
    outbox.finish(attempt.attempt_id, outcome=ACCEPTED_VERIFIED,
                  now=T + timedelta(seconds=2), external_message_id="m1",
                  verification_source="provider", observed_at=T + timedelta(seconds=2))
    assert outbox.get_intent(envelope, route="QUANT_UPSTREAM_REPORT",
                             target_type="desk", target_id="quant-desk-1").status == ACCEPTED_VERIFIED
    assert outbox.get_intent(other, route="QUANT_UPSTREAM_REPORT",
                             target_type="desk", target_id="quant-desk-1").status == READY_OFFLINE
    assert len(outbox.get_attempts(envelope, route="QUANT_UPSTREAM_REPORT",
                                   target_type="desk", target_id="quant-desk-1")) == 1
    assert len(outbox.get_attempts(other, route="QUANT_UPSTREAM_REPORT",
                                   target_type="desk", target_id="quant-desk-1")) == 0


def test_claim_windows_and_token_reuse_fail_closed():
    outbox = RouteOutbox()
    envelope = sample()
    plan(outbox, envelope)
    with pytest.raises(OutboxError, match="LEASE_WINDOW_INVALID"):
        claim(outbox, envelope, lease_until=envelope.valid_until)
    with pytest.raises(OutboxError, match="CLAIM_WINDOW_INVALID"):
        claim(outbox, envelope, claim_at=envelope.valid_until - timedelta(seconds=1))
    first = claim(outbox, envelope)
    with pytest.raises(OutboxError, match="INTENT_NOT_CLAIMABLE"):
        claim(outbox, envelope, token="tok-2")
    outbox.finish(first.attempt_id, outcome=UNKNOWN, now=T + timedelta(seconds=1))
    with pytest.raises(OutboxError, match="INTENT_NOT_CLAIMABLE"):
        claim(outbox, envelope, token="tok-3")
