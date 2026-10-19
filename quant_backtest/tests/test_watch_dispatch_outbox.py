"""G1 offline evidence store and three-route outbox tests. No external effects."""
from __future__ import annotations

from dataclasses import replace
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
ONE_SECOND = timedelta(seconds=1)


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


def test_epoch_floor_and_stale_replanning_after_revoke():
    outbox = RouteOutbox()
    env = sample()
    other = sample(event_id="ev2")
    assert outbox.epoch_floor(env) == outbox.epoch_floor(other) == 0
    plan(outbox, env, control_epoch=2)
    assert outbox.epoch_floor(env) == 2
    assert outbox.epoch_floor(other) == 0
    outbox.revoke_envelope(env, epoch=3, now=T + timedelta(seconds=1))
    assert outbox.epoch_floor(env) == 3
    with pytest.raises(OutboxError, match="EPOCH_STALE"):
        plan(outbox, env, control_epoch=2)
    with pytest.raises(OutboxError, match="EPOCH_STALE"):
        plan(outbox, env, target_id="fresh", intent_id="fresh", control_epoch=3,
             created_at=T + timedelta(seconds=2))
    assert plan(outbox, env, target_id="fresh", intent_id="fresh",
                control_epoch=4, created_at=T + timedelta(seconds=2)).status == READY_OFFLINE
    assert outbox.epoch_floor(env) == 4
    with pytest.raises(OutboxError, match="RETRY_KEY_REVOKED"):
        plan(outbox, env, intent_id="intent-g1", generation=1,
             control_epoch=5, created_at=T + timedelta(seconds=3))
    with pytest.raises(OutboxError, match="RETRY_KEY_REVOKED"):
        plan(outbox, env, control_epoch=5,
             created_at=T + timedelta(seconds=3))


def test_unknown_supersede_preserves_audit_and_rejects_invalid_generations():
    from quant_backtest.watch_dispatch.route_outbox import ReportFingerprint

    outbox = RouteOutbox()
    env = sample()
    fingerprint = ReportFingerprint("report-1", "ANOMALY", env.event_id,
                                    env.evidence_version, env.envelope_hash,
                                    "watch-1", "desk1", T)
    first = plan(outbox, env, report_fingerprint=fingerprint)
    a1 = claim(outbox, env)
    outbox.finish(a1.attempt_id, outcome=UNKNOWN, now=T + ONE_SECOND)
    with pytest.raises(OutboxError, match="RETRY_GENERATION_INVALID"):
        plan(outbox, env, intent_id="intent-g2", generation=2,
             report_fingerprint=fingerprint)
    with pytest.raises(OutboxError, match="INTENT_ID_REUSE"):
        plan(outbox, env, generation=1, report_fingerprint=fingerprint)
    with pytest.raises(OutboxError, match="INTENT_CONFLICT"):
        plan(outbox, env, intent_id="intent-g0", generation=0)
    second = plan(outbox, env, intent_id="intent-g1", generation=1,
                  created_at=T + 2 * ONE_SECOND,
                  report_fingerprint=fingerprint)
    assert second.intent_id == "intent-g1"
    assert outbox.get_intent(env, route="QUANT_UPSTREAM_REPORT",
                             target_type="desk", target_id="desk1") is second
    key = second.idempotency_key(env)
    assert len(outbox._intent_history[key]) == 1
    assert outbox._intent_history[key][0].intent_id == first.intent_id
    assert outbox._intent_history[key][0].status == UNKNOWN
    assert outbox._generations[key] == 1
    assert outbox.get_attempts(env, route="QUANT_UPSTREAM_REPORT",
                               target_type="desk", target_id="desk1")[0].attempt_id == a1.attempt_id
    assert plan(outbox, env, intent_id="intent-g1", generation=1,
                created_at=T + 2 * ONE_SECOND,
                report_fingerprint=fingerprint) is second
    with pytest.raises(OutboxError, match="INTENT_CONFLICT"):
        plan(outbox, env, intent_id="intent-g3", generation=3)


def test_direct_supersede_without_terminal_attempt_is_denied():
    from quant_backtest.watch_dispatch.route_outbox import ReportFingerprint

    outbox = RouteOutbox()
    env = sample()
    fingerprint = ReportFingerprint("report-1", "ANOMALY", env.event_id,
                                    env.evidence_version, env.envelope_hash,
                                    "watch-1", "desk1", T)
    first = plan(outbox, env, report_fingerprint=fingerprint)
    key = first.idempotency_key(env)
    outbox._intents[key] = replace(first, status=UNKNOWN)
    with pytest.raises(OutboxError, match="RETRY_NOT_PERMITTED"):
        plan(outbox, env, intent_id="intent-g1", generation=1,
             created_at=T + ONE_SECOND, report_fingerprint=fingerprint)
    assert outbox._intents[key].intent_id == "intent1"
    assert outbox._intent_history[key] == []


def test_report_fingerprint_registration_and_direct_supersede_guards():
    from quant_backtest.watch_dispatch.route_outbox import ReportFingerprint

    outbox = RouteOutbox()
    env = sample()
    fingerprint = ReportFingerprint("report-1", "ANOMALY", env.event_id,
                                    env.evidence_version, env.envelope_hash,
                                    "watch-1", "desk1", T)
    first = outbox.plan(env, route="QUANT_UPSTREAM_REPORT", target_type="desk",
                        target_id="desk1", policy_version="p1", control_epoch=0,
                        intent_id="intent1", created_at=T,
                        valid_until=env.valid_until - timedelta(minutes=1),
                        report_fingerprint=fingerprint)
    key = first.idempotency_key(env)
    assert outbox._report_fingerprints[key] == fingerprint
    assert outbox._origin_created_at[key] == T
    initial = claim(outbox, env)
    outbox.finish(initial.attempt_id, outcome=UNKNOWN, now=T + ONE_SECOND)
    with pytest.raises(OutboxError, match="REPORT_FINGERPRINT_REQUIRED"):
        plan(outbox, env, intent_id="intent-g1", generation=1,
             created_at=T + 2 * ONE_SECOND)
    with pytest.raises(OutboxError, match="REPORT_FINGERPRINT_MISMATCH"):
        plan(outbox, env, intent_id="intent-g1", generation=1,
             report_fingerprint=replace(fingerprint, report_id="other"),
             created_at=T + 2 * ONE_SECOND)
    with pytest.raises(OutboxError, match="REPORT_FINGERPRINT_MISMATCH"):
        plan(outbox, env, intent_id="intent-g1", generation=1,
             report_fingerprint=replace(fingerprint, sender_agent_id="other"),
             created_at=T + 2 * ONE_SECOND)
    with pytest.raises(OutboxError, match="REPORT_FINGERPRINT_MISMATCH"):
        plan(outbox, env, intent_id="intent-g1", generation=1,
             report_fingerprint=replace(fingerprint, created_at=T + ONE_SECOND),
             created_at=T + 2 * ONE_SECOND)
    with pytest.raises(OutboxError, match="RETRY_EPOCH_CHANGED"):
        plan(outbox, env, intent_id="intent-g1", generation=1,
             control_epoch=1, report_fingerprint=fingerprint,
             created_at=T + 2 * ONE_SECOND)
    with pytest.raises(OutboxError, match="ROUTE_TIME_INVALID"):
        plan(outbox, env, intent_id="intent-g1", generation=1,
             created_at=T, report_fingerprint=fingerprint)
    assert outbox._report_fingerprints[key] is fingerprint
    assert len(outbox._intent_history[key]) == 0
    successor = plan(outbox, env, intent_id="intent-g1", generation=1,
                     created_at=T + 2 * ONE_SECOND,
                     report_fingerprint=fingerprint)
    assert successor.created_at == T + 2 * ONE_SECOND
    assert outbox._origin_created_at[key] == T
    assert outbox._report_fingerprints[key] is fingerprint
    with pytest.raises(OutboxError, match="REPORT_FINGERPRINT_REQUIRED"):
        plan(outbox, env, intent_id="intent-g1", generation=1,
             created_at=T + 2 * ONE_SECOND)


def test_revoke_closes_claimed_attempts_atomically():
    outbox = RouteOutbox()
    env = sample()
    other = sample(event_id="ev2")
    plan(outbox, env, intent_id="first")
    plan(outbox, env, target_id="other", intent_id="second")
    plan(outbox, other, intent_id="independent")
    first = claim(outbox, env)
    second = claim(outbox, env, target_id="other", token="other-token")
    outbox.revoke_envelope(env, epoch=1, now=T + ONE_SECOND)
    for attempt in (first, second):
        finished = outbox._attempts[attempt.attempt_id]
        assert finished.outcome == UNKNOWN and finished.receipt is None
        assert finished.attempt_no == 1
        with pytest.raises(OutboxError, match="ATTEMPT_NOT_CURRENT"):
            outbox.finish(attempt.attempt_id, outcome=UNKNOWN, now=T + 2 * ONE_SECOND)
    assert outbox._attempt_nos[first.idempotency_key] == 1
    assert outbox.get_intent(other, route="QUANT_UPSTREAM_REPORT",
                             target_type="desk", target_id="desk1").status == READY_OFFLINE


@pytest.mark.parametrize("corruption", ["missing", "duplicate", "different-intent"])
def test_revoke_preflight_rejects_corrupt_attempt_atomically(corruption):
    outbox = RouteOutbox()
    env = sample()
    plan(outbox, env, intent_id="first")
    plan(outbox, env, target_id="other", intent_id="second")
    first = claim(outbox, env)
    second = claim(outbox, env, target_id="other", token="other-token")
    if corruption == "missing":
        del outbox._attempts[first.attempt_id]
    elif corruption == "duplicate":
        outbox._attempts["duplicate"] = replace(first, attempt_id="duplicate")
    else:
        outbox._attempts[first.attempt_id] = replace(first, intent_id="foreign")
    before_attempts = dict(outbox._attempts)
    before_intents = dict(outbox._intents)
    before_epochs = dict(outbox._epochs)
    before_nos = dict(outbox._attempt_nos)
    with pytest.raises(OutboxError, match="REVOKE_ATTEMPT_INCONSISTENT"):
        outbox.revoke_envelope(env, epoch=1, now=T + ONE_SECOND)
    assert outbox._attempts == before_attempts
    assert outbox._intents == before_intents
    assert outbox._epochs == before_epochs
    assert outbox._attempt_nos == before_nos
    assert outbox._revoked == set()
    assert outbox._attempts[second.attempt_id].outcome == CLAIMED


def test_supersede_attempt_budget_shared_across_three_generations():
    from quant_backtest.watch_dispatch.route_outbox import ReportFingerprint

    outbox = RouteOutbox()
    env = sample()
    fingerprint = ReportFingerprint("report-1", "ANOMALY", env.event_id,
                                    env.evidence_version, env.envelope_hash,
                                    "watch-1", "desk1", T)
    plan(outbox, env, report_fingerprint=fingerprint)
    for generation in range(3):
        if generation:
            plan(outbox, env, intent_id=f"intent-g{generation}",
                 generation=generation, created_at=T + generation * ONE_SECOND,
                 report_fingerprint=fingerprint)
        attempt = claim(outbox, env, token=f"token-{generation}",
                        claim_at=T + (generation + 2) * ONE_SECOND)
        assert attempt.attempt_no == generation + 1
        outbox.finish(attempt.attempt_id, outcome=UNKNOWN,
                      now=T + (generation + 3) * ONE_SECOND)
    plan(outbox, env, intent_id="intent-g3", generation=3,
         created_at=T + 4 * ONE_SECOND, report_fingerprint=fingerprint)
    with pytest.raises(OutboxError, match="ATTEMPTS_EXHAUSTED"):
        claim(outbox, env, token="token-4", claim_at=T + 5 * ONE_SECOND)


@pytest.mark.parametrize("observed_delta,accepted", [
    (-1, False), (0, True), (2, True), (6, True), (7, False),
])
def test_double_anchor_receipt_window(observed_delta, accepted):
    outbox = RouteOutbox()
    env = sample()
    plan(outbox, env)
    attempt = claim(outbox, env, claim_at=T + 3 * ONE_SECOND)
    finished = outbox.finish(
        attempt.attempt_id, outcome=ACCEPTED_VERIFIED,
        now=T + 6 * ONE_SECOND, external_message_id="message-1",
        verification_source="port", observed_at=T + observed_delta * ONE_SECOND)
    assert (finished.outcome == ACCEPTED_VERIFIED) is accepted
    assert (finished.receipt is not None) is accepted


def test_closed_intent_cannot_be_superseded():
    outbox = RouteOutbox()
    env = sample()
    plan(outbox, env)
    attempt = claim(outbox, env)
    outbox.finish(attempt.attempt_id, outcome=ACCEPTED_VERIFIED,
                  now=T + ONE_SECOND, external_message_id="ack",
                  verification_source="port", observed_at=T)
    with pytest.raises(OutboxError, match="INTENT_CONFLICT"):
        plan(outbox, env, intent_id="intent-g1", generation=1)


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
