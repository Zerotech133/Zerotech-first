"""G1+G2 end-to-end offline integration tests over frozen modules only.

Links evidence store, single-upstream reporting outbox, dual-gate, shadow
ledger and projections without modifying any of them. Watch reports upward to
the quant trading department only; no send and no dispatch happens here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from quant_backtest.quant_sector import (
    AuthorizationScope, HumanAuthorization, PermissionGate, ResearchReview,
    RiskDecision,
)
from quant_backtest.watch_dispatch import (
    EvidenceStore, RouteOutbox, ShadowDecision, ShadowLedger,
    ShadowReview, freeze_offline,
)
from quant_backtest.quant_sector.projections import gate_projection
from quant_backtest.watch_dispatch.projections import (
    evidence_projection, outbox_projection, shadow_projection,
)

T = datetime(2026, 10, 19, 2, 0, tzinfo=timezone.utc)
H = "a" * 64
ROUTES = (("QUANT_UPSTREAM_REPORT", "desk", "quant-desk-1"),)


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


def build_chain(envelope):
    """Freeze, plan all three routes, confirm review, record authorization."""
    store = EvidenceStore()
    outbox = RouteOutbox()
    gate = PermissionGate(acceptance_lookup=lambda ref: ref == "rcpt-1")
    ledger = ShadowLedger()
    store.freeze(envelope)
    for route, target_type, target_id in ROUTES:
        outbox.plan(envelope, route=route, target_type=target_type,
                    target_id=target_id, policy_version="p1", control_epoch=0,
                    intent_id=f"i-{route}", created_at=envelope.freeze_at,
                    valid_until=envelope.valid_until - timedelta(minutes=1))
    gate.submit_review(envelope, ResearchReview(
        review_id="rev-1", event_id=envelope.event_id,
        evidence_version=envelope.evidence_version,
        envelope_hash=envelope.envelope_hash,
        reviewer_external_identity="analyst-1", decision="CONFIRMED",
        recorded_at=envelope.freeze_at))
    gate.record_authorization(envelope, HumanAuthorization(
        authorization_id="auth-1", event_id=envelope.event_id,
        evidence_version=envelope.evidence_version,
        envelope_hash=envelope.envelope_hash, principal_id="trader-1",
        external_authority_ref="authority:1", auth_epoch=0, review_id="rev-1",
        scope=AuthorizationScope(market=envelope.market, code=envelope.code,
                                 side="BUY", max_quantity=100, sim_only=True),
        issued_at=envelope.freeze_at, valid_from=envelope.freeze_at,
        valid_until=envelope.valid_until - timedelta(minutes=5)))
    ledger.append_decision(envelope, ShadowDecision(
        shadow_id="sh-1", event_id=envelope.event_id,
        evidence_version=envelope.evidence_version,
        envelope_hash=envelope.envelope_hash, decision_at=envelope.freeze_at,
        hypothesis="breakout", planned_action="observe entry",
        invalidation_condition="close below baseline",
        visible_evidence_refs=("raw:1",)))
    return store, outbox, gate, ledger


class PassAuthority:
    def __init__(self, auth):
        self.auth = auth

    def resolve_and_verify(self, authorization_id, envelope, now):
        return self.auth if authorization_id == self.auth.authorization_id else None


class PassRisk:
    def __init__(self, envelope, auth_id="auth-1"):
        self.decision = RiskDecision(
            decision_id="risk-1", event_id=envelope.event_id,
            evidence_version=envelope.evidence_version,
            envelope_hash=envelope.envelope_hash, authorization_id=auth_id,
            decision="PASS", policy_version="rg-1", as_of=envelope.freeze_at,
            valid_until=envelope.valid_until - timedelta(minutes=5),
            external_verification_ref="rg-verify-1")

    def evaluate_independently(self, envelope, authorization, now):
        return self.decision


def test_full_offline_chain_end_to_end():
    envelope = sample()
    store, outbox, gate, ledger = build_chain(envelope)
    attempt = outbox.claim(envelope, route="QUANT_UPSTREAM_REPORT", target_type="desk",
                           target_id="quant-desk-1", claim_token="tok-1",
                           claim_at=envelope.freeze_at,
                           lease_until=envelope.valid_until - timedelta(minutes=5))
    outbox.finish(attempt.attempt_id, outcome="ACCEPTED_VERIFIED",
                  now=envelope.freeze_at + timedelta(seconds=30),
                  external_message_id="msg-1", verification_source="provider:quant-desk",
                  observed_at=envelope.freeze_at + timedelta(seconds=10))
    verdict = gate.consume(envelope, authorization_id="auth-1",
                           now=envelope.freeze_at + timedelta(minutes=1),
                           acceptance_ref="rcpt-1",
                           authority_port=PassAuthority(
                               gate._authorizations["auth-1"]),
                           risk_port=PassRisk(envelope))
    assert verdict.status == "SIM_DISPATCH_ELIGIBLE"
    assert verdict.dispatch_permitted is False
    ledger.append_review(envelope, ShadowReview(
        review_id="sr-1", shadow_id="sh-1",
        reviewed_at=envelope.freeze_at + timedelta(minutes=2),
        posterior_refs=("post:1",), hindsight_note="held"))
    rows = outbox_projection(envelope, outbox, ROUTES)
    assert rows[0]["route"] == "QUANT_UPSTREAM_REPORT"
    assert rows[0]["status"] == "ACCEPTED_VERIFIED"
    assert shadow_projection(envelope, ledger,
                             envelope.freeze_at + timedelta(minutes=1))[0][
        "visible_review_ids"] == ()
    assert evidence_projection(envelope, store)["stored"] is True
    assert gate_projection(envelope, gate)["connection"] == "NOT_CONNECTED"


def test_expired_evidence_closes_every_domain():
    envelope = sample()
    store, outbox, gate, ledger = build_chain(envelope)
    expired = envelope.valid_until
    with pytest.raises(ValueError):
        outbox.claim(envelope, route="QUANT_UPSTREAM_REPORT", target_type="desk",
                     target_id="quant-desk-1", claim_token="tok-x", claim_at=expired,
                     lease_until=expired + timedelta(minutes=1))
    with pytest.raises(ValueError):
        ledger.append_decision(envelope, ShadowDecision(
            shadow_id="sh-2", event_id=envelope.event_id,
            evidence_version=envelope.evidence_version,
            envelope_hash=envelope.envelope_hash, decision_at=expired,
            hypothesis="h", planned_action="p", invalidation_condition="i",
            visible_evidence_refs=("raw:1",)))
    verdict = gate.consume(envelope, authorization_id="auth-1", now=expired,
                           acceptance_ref="rcpt-1",
                           authority_port=PassAuthority(gate._authorizations["auth-1"]),
                           risk_port=PassRisk(envelope))
    assert verdict.status == "BLOCKED" and verdict.code == "EVIDENCE_NOT_CURRENT"


def test_gate_revocation_does_not_touch_outbox_accounts():
    envelope = sample()
    store, outbox, gate, ledger = build_chain(envelope)
    gate.revoke_envelope(envelope, epoch=1, now=envelope.freeze_at + timedelta(seconds=1))
    before = outbox.get_intent(envelope, route="QUANT_UPSTREAM_REPORT", target_type="desk",
                               target_id="quant-desk-1")
    verdict = gate.consume(envelope, authorization_id="auth-1",
                           now=envelope.freeze_at + timedelta(minutes=1),
                           acceptance_ref="rcpt-1",
                           authority_port=PassAuthority(gate._authorizations["auth-1"]),
                           risk_port=PassRisk(envelope))
    after = outbox.get_intent(envelope, route="QUANT_UPSTREAM_REPORT", target_type="desk",
                              target_id="quant-desk-1")
    assert verdict.status == "BLOCKED" and verdict.code == "IDENTITY_BLOCKED"
    assert before == after and after.status == "READY_OFFLINE"


def test_wrong_identity_envelope_isolated_across_domains():
    envelope = sample()
    store, outbox, gate, ledger = build_chain(envelope)
    twin = sample(event_id="ev2")
    assert evidence_projection(twin, store)["stored"] is False
    assert outbox_projection(twin, outbox, ROUTES)[0]["status"] == "UNPLANNED"
    assert shadow_projection(twin, ledger, T) == ()
    verdict = gate.consume(twin, authorization_id="auth-1", now=T + timedelta(minutes=1),
                           acceptance_ref="rcpt-1",
                           authority_port=PassAuthority(gate._authorizations["auth-1"]),
                           risk_port=PassRisk(twin))
    assert verdict.status == "BLOCKED" and verdict.code == "AUTH_IDENTITY_MISMATCH"


def test_upstream_report_receipt_states_stay_independent_per_envelope():
    envelope = sample()
    other = sample(event_id="ev2")
    store, outbox, gate, ledger = build_chain(envelope)
    store.freeze(other)
    outbox.plan(other, route="QUANT_UPSTREAM_REPORT", target_type="desk",
                target_id="quant-desk-1", policy_version="p1", control_epoch=0,
                intent_id="i-other", created_at=T, valid_until=T + timedelta(minutes=29))
    first = outbox.claim(envelope, route="QUANT_UPSTREAM_REPORT", target_type="desk",
                         target_id="quant-desk-1", claim_token="tok-e",
                         claim_at=envelope.freeze_at,
                         lease_until=envelope.valid_until - timedelta(minutes=5))
    outbox.finish(first.attempt_id, outcome="UNKNOWN",
                  now=envelope.freeze_at + timedelta(minutes=6))
    second = outbox.claim(other, route="QUANT_UPSTREAM_REPORT", target_type="desk",
                          target_id="quant-desk-1", claim_token="tok-r",
                          claim_at=T, lease_until=T + timedelta(minutes=25))
    outbox.finish(second.attempt_id, outcome="DEFINITELY_REJECTED",
                  now=T + timedelta(seconds=30), external_message_id="rej-1",
                  verification_source="provider:quant-desk",
                  observed_at=T + timedelta(seconds=20))
    assert outbox.get_intent(envelope, route="QUANT_UPSTREAM_REPORT",
                             target_type="desk", target_id="quant-desk-1").status == "UNKNOWN"
    assert outbox.get_intent(other, route="QUANT_UPSTREAM_REPORT",
                             target_type="desk", target_id="quant-desk-1").status == "READY_OFFLINE"
    assert len(outbox.get_attempts(other, route="QUANT_UPSTREAM_REPORT",
                                   target_type="desk", target_id="quant-desk-1")) == 1
    assert len(outbox.get_attempts(envelope, route="QUANT_UPSTREAM_REPORT",
                                   target_type="desk", target_id="quant-desk-1")) == 1
