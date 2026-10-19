"""Public, offline adversarial tests for rejection receipt authority."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from quant_backtest.tests.test_watch_dispatch_outbox import T, claim, plan, sample
from quant_backtest.watch_dispatch.route_outbox import (
    CLAIMED, DEFINITELY_REJECTED, READY_OFFLINE, UNKNOWN,
    OutboxError, ReportFingerprint, RouteOutbox,
)


def _fingerprint(envelope):
    """Create the immutable report identity used by the independent port."""
    return ReportFingerprint("report-1", "ANOMALY", envelope.event_id,
                             envelope.evidence_version, envelope.envelope_hash,
                             "watch-1", "desk1", T)


class BoundRejection:
    """Independent in-memory rejection ledger, not a finish-argument echo."""

    def __init__(self, fingerprint, identity):
        self.fingerprint = fingerprint
        self.identity = identity
        self.events = set()
        self.on_verify = None

    def active_rejection(self, fingerprint, attempt, intent, identity,
                         generation, rejection_ref, verification_source,
                         observed_at, *, now):
        if self.on_verify is not None:
            self.on_verify()
        return (fingerprint == self.fingerprint and identity == self.identity
                and (attempt.attempt_id, intent.intent_id, intent.control_epoch,
                     generation, rejection_ref, verification_source,
                     observed_at) in self.events
                and attempt.outcome == CLAIMED
                and attempt.intent_id == intent.intent_id
                and attempt.route == intent.route == "QUANT_UPSTREAM_REPORT"
                and attempt.claim_at <= observed_at <= now)


def _setup(port=True, fingerprint=True):
    envelope = sample()
    bound = BoundRejection(_fingerprint(envelope), RouteOutbox._identity(envelope))
    outbox = RouteOutbox(active_rejection_port=bound if port else None)
    plan(outbox, envelope,
         report_fingerprint=bound.fingerprint if fingerprint else None)
    attempt = claim(outbox, envelope)
    intent = outbox.get_intent(envelope, route="QUANT_UPSTREAM_REPORT",
                               target_type="desk", target_id="desk1")
    return envelope, bound, outbox, attempt, intent


def _finish(outbox, attempt, *, ref="rejection-1", source="ledger-1",
            observed_at=None):
    return outbox.finish(
        attempt.attempt_id, outcome=DEFINITELY_REJECTED,
        now=T + timedelta(seconds=10), external_message_id=ref,
        verification_source=source,
        observed_at=observed_at or T + timedelta(seconds=5))


@pytest.mark.parametrize("port,fingerprint", [(False, True), (True, False),
                                                 (False, False)])
def test_missing_authority_or_fingerprint_never_reopens(port, fingerprint):
    env, bound, outbox, attempt, intent = _setup(port, fingerprint)
    bound.events.add((attempt.attempt_id, intent.intent_id, 0, 0, "rejection-1",
                      "ledger-1", T + timedelta(seconds=5)))
    finished = _finish(outbox, attempt)
    assert finished.outcome == UNKNOWN and finished.receipt is None
    assert outbox.get_intent(env, route="QUANT_UPSTREAM_REPORT", target_type="desk",
                             target_id="desk1").status == UNKNOWN
    with pytest.raises(OutboxError, match="INTENT_NOT_CLAIMABLE"):
        claim(outbox, env, token="again")


@pytest.mark.parametrize("mutation", ["ref", "source", "time", "generation",
                                      "identity", "epoch", "attempt", "fingerprint"])
def test_forged_rejection_fields_fail_closed(mutation):
    env, bound, outbox, attempt, intent = _setup()
    event = (attempt.attempt_id, intent.intent_id, 0, 0, "rejection-1",
             "ledger-1", T + timedelta(seconds=5))
    bound.events.add(event)
    if mutation == "ref":
        result = _finish(outbox, attempt, ref="forged")
    elif mutation == "source":
        result = _finish(outbox, attempt, source="forged")
    elif mutation == "time":
        result = _finish(outbox, attempt, observed_at=T + timedelta(seconds=6))
    else:
        if mutation == "generation":
            bound.events = {(event[0], event[1], event[2], 1, *event[4:])}
        elif mutation == "identity":
            bound.identity = ("foreign",)
        elif mutation == "epoch":
            bound.events = {(event[0], event[1], 1, *event[3:])}
        elif mutation == "attempt":
            bound.events = {("foreign", *event[1:])}
        else:
            bound.fingerprint = replace(bound.fingerprint, report_id="foreign")
        result = _finish(outbox, attempt)
    assert result.outcome == UNKNOWN and result.receipt is None
    assert outbox.get_intent(env, route="QUANT_UPSTREAM_REPORT", target_type="desk",
                             target_id="desk1").status == UNKNOWN


@pytest.mark.parametrize("invalid_time", [None, "not-a-time",
                                               T - timedelta(seconds=1),
                                               T + timedelta(seconds=11)])
def test_invalid_rejection_event_time_returns_unknown(invalid_time):
    env, bound, outbox, attempt, intent = _setup()
    result = outbox.finish(
        attempt.attempt_id, outcome=DEFINITELY_REJECTED,
        now=T + timedelta(seconds=10), external_message_id="rejection-1",
        verification_source="ledger-1", observed_at=invalid_time)
    assert result.outcome == UNKNOWN and result.receipt is None
    assert outbox.get_intent(env, route="QUANT_UPSTREAM_REPORT", target_type="desk",
                             target_id="desk1").status == UNKNOWN


def test_authority_exception_returns_unknown():
    env, bound, outbox, attempt, intent = _setup()

    def broken():
        raise RuntimeError("authority offline")

    bound.on_verify = broken
    result = _finish(outbox, attempt)
    assert result.outcome == UNKNOWN and result.receipt is None
    assert outbox.get_intent(env, route="QUANT_UPSTREAM_REPORT", target_type="desk",
                             target_id="desk1").status == UNKNOWN


def test_callback_revoke_cannot_restore_ready_or_receipt():
    env, bound, outbox, attempt, intent = _setup()
    bound.events.add((attempt.attempt_id, intent.intent_id, 0, 0, "rejection-1",
                      "ledger-1", T + timedelta(seconds=5)))

    def revoke_after_event_check():
        assert (attempt.attempt_id, intent.intent_id, 0, 0, "rejection-1",
                "ledger-1", T + timedelta(seconds=5)) in bound.events
        outbox.revoke_envelope(env, epoch=1, now=T + timedelta(seconds=9))

    bound.on_verify = revoke_after_event_check
    result = _finish(outbox, attempt)
    assert result.outcome == UNKNOWN and result.receipt is None
    assert outbox.get_intent(env, route="QUANT_UPSTREAM_REPORT", target_type="desk",
                             target_id="desk1").status == UNKNOWN
    assert outbox._attempts[attempt.attempt_id] is result
    assert outbox.epoch_floor(env) == 1


def test_bound_rejection_retries_at_most_three_times():
    env, bound, outbox, attempt, intent = _setup()
    for attempt_no in (1, 2, 3):
        if attempt_no > 1:
            attempt = claim(outbox, env, token=f"token-{attempt_no}")
            intent = outbox.get_intent(env, route="QUANT_UPSTREAM_REPORT",
                                       target_type="desk", target_id="desk1")
        observed_at = T + timedelta(seconds=attempt_no * 5)
        bound.events.add((attempt.attempt_id, intent.intent_id, 0, 0,
                          f"rejection-{attempt_no}", "ledger-1", observed_at))
        result = outbox.finish(
            attempt.attempt_id, outcome=DEFINITELY_REJECTED,
            now=T + timedelta(seconds=attempt_no * 10),
            external_message_id=f"rejection-{attempt_no}",
            verification_source="ledger-1", observed_at=observed_at)
        assert result.outcome == DEFINITELY_REJECTED
        assert result.receipt is not None
    with pytest.raises(OutboxError, match="ATTEMPTS_EXHAUSTED"):
        claim(outbox, env, token="token-4")


def test_exact_rejection_receipt_reopens_only_current_generation():
    env, bound, outbox, attempt, intent = _setup()
    bound.events.add((attempt.attempt_id, intent.intent_id, 0, 0, "rejection-1",
                      "ledger-1", T + timedelta(seconds=5)))
    result = _finish(outbox, attempt)
    assert result.outcome == DEFINITELY_REJECTED
    assert result.receipt.external_message_id == "rejection-1"
    assert outbox.get_intent(env, route="QUANT_UPSTREAM_REPORT", target_type="desk",
                             target_id="desk1").status == READY_OFFLINE
