"""G2 offline dual-gate state machine and shadow ledger tests. No dispatch."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from quant_backtest.quant_sector import (
    AuthorizationScope, HumanAuthorization, PermissionGate, ResearchReview,
    RiskDecision,
)
from quant_backtest.quant_sector.dual_gate import GateError
from quant_backtest.watch_dispatch import (
    ShadowDecision, ShadowLedger, ShadowReview, freeze_offline,
)

T = datetime(2026, 10, 19, 2, 0, tzinfo=timezone.utc)
H = "a" * 64
M5 = timedelta(minutes=5)


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


def review(envelope, *, decision="CONFIRMED", recorded_at=None, review_id="rev-1"):
    return ResearchReview(
        review_id=review_id, event_id=envelope.event_id,
        evidence_version=envelope.evidence_version,
        envelope_hash=envelope.envelope_hash,
        reviewer_external_identity="analyst-1", decision=decision,
        recorded_at=recorded_at or envelope.freeze_at)


def scope(envelope, **changes):
    fields = dict(market=envelope.market, code=envelope.code, side="BUY",
                  max_quantity=100, sim_only=True)
    fields.update(changes)
    return AuthorizationScope(**fields)


def authorization(envelope, *, review_id="rev-1", epoch=0, issued_at=None,
                  auth_id="auth-1", auth_scope=None, valid_until=None):
    return HumanAuthorization(
        authorization_id=auth_id, event_id=envelope.event_id,
        evidence_version=envelope.evidence_version,
        envelope_hash=envelope.envelope_hash, principal_id="trader-1",
        external_authority_ref="authority:1", auth_epoch=epoch,
        review_id=review_id, scope=auth_scope or scope(envelope),
        issued_at=issued_at or envelope.freeze_at,
        valid_from=issued_at or envelope.freeze_at,
        valid_until=valid_until or envelope.valid_until - M5)


def risk(envelope, *, auth_id="auth-1", decision="PASS", as_of=None,
         valid_until=None):
    return RiskDecision(
        decision_id="risk-1", event_id=envelope.event_id,
        evidence_version=envelope.evidence_version,
        envelope_hash=envelope.envelope_hash, authorization_id=auth_id,
        decision=decision, policy_version="rg-1",
        as_of=as_of or envelope.freeze_at,
        valid_until=valid_until or envelope.valid_until - M5,
        external_verification_ref="rg-verify-1")


class MemoryAuthority:
    def __init__(self, auth):
        self.auth = auth

    def resolve_and_verify(self, authorization_id, envelope, now):
        if authorization_id != self.auth.authorization_id:
            return None
        return self.auth


class DenyAuthority(MemoryAuthority):
    def resolve_and_verify(self, authorization_id, envelope, now):
        return None


class MemoryRiskGuard:
    def __init__(self, decision):
        self.decision = decision

    def evaluate_independently(self, envelope, authorization, now):
        if authorization.authorization_id != self.decision.authorization_id:
            return None
        return self.decision


def gated_envelope():
    envelope = sample()
    gate = PermissionGate()
    assert gate.submit_review(envelope, review(envelope)) == "REVIEW_CONFIRMED"
    gate.record_authorization(envelope, authorization(envelope))
    return envelope, gate


def test_dual_gates_pass_and_verdict_cannot_dispatch():
    envelope, gate = gated_envelope()
    verdict = gate.consume(envelope, authorization_id="auth-1", now=T + M5,
                           authority_port=MemoryAuthority(authorization(envelope)),
                           risk_port=MemoryRiskGuard(risk(envelope)))
    assert verdict.status == "SIM_DISPATCH_ELIGIBLE"
    assert verdict.dispatch_permitted is False
    assert verdict.trading_permitted is False
    assert gate.dispatch_permitted is False
    assert gate.trading_permitted is False


def test_recheck_at_consume_blocks_revoked_or_denied():
    envelope, gate = gated_envelope()
    auth = authorization(envelope)
    verdict = gate.consume(envelope, authorization_id="auth-1", now=T + M5,
                           authority_port=DenyAuthority(auth),
                           risk_port=MemoryRiskGuard(risk(envelope)))
    assert verdict.status == "BLOCKED" and verdict.code == "AUTH_UNVERIFIED"
    verdict = gate.consume(envelope, authorization_id="auth-1", now=T + M5,
                           authority_port=MemoryAuthority(auth),
                           risk_port=MemoryRiskGuard(
                               risk(envelope, decision="DENY", as_of=T + M5)))
    assert verdict.status == "BLOCKED" and verdict.code == "RISK_DENY"


@pytest.mark.parametrize("mutate, code", [
    (lambda env: authorization(env, auth_scope=scope(env, code="600001")), None),
    (lambda env: authorization(env, auth_scope=scope(env, sim_only=False)), None),
    (lambda env: authorization(env, valid_until=env.valid_until + M5), None),
])
def test_record_authorization_fail_closed(mutate, code):
    envelope = sample()
    gate = PermissionGate()
    gate.submit_review(envelope, review(envelope))
    with pytest.raises(GateError):
        gate.record_authorization(envelope, mutate(envelope))


def test_review_alone_never_grants_anything():
    envelope = sample()
    gate = PermissionGate()
    assert gate.submit_review(envelope, review(envelope)) == "REVIEW_CONFIRMED"
    verdict = gate.consume(envelope, authorization_id="auth-1", now=T + M5,
                           authority_port=DenyAuthority(authorization(envelope)),
                           risk_port=MemoryRiskGuard(risk(envelope)))
    assert verdict.status == "BLOCKED" and verdict.code == "AUTH_UNVERIFIED"
    assert verdict.production_permitted is False
    assert verdict.notification_permitted is False


def test_record_authorization_requires_confirmed_review():
    envelope = sample()
    gate = PermissionGate()
    with pytest.raises(GateError, match="AUTH_REQUIRES_CONFIRMED_REVIEW"):
        gate.record_authorization(envelope, authorization(envelope))
    gate.submit_review(envelope, review(envelope, decision="NEEDS_EVIDENCE",
                                        review_id="rev-n"))
    with pytest.raises(GateError, match="AUTH_REQUIRES_CONFIRMED_REVIEW"):
        gate.record_authorization(envelope, authorization(envelope))


def test_rejected_review_blocks_identity():
    envelope = sample()
    gate = PermissionGate()
    assert gate.submit_review(envelope, review(envelope, decision="REJECTED")) == "BLOCKED"
    with pytest.raises(GateError, match="IDENTITY_BLOCKED"):
        gate.record_authorization(envelope, authorization(envelope))
    verdict = gate.consume(envelope, authorization_id="auth-1", now=T + M5,
                           authority_port=MemoryAuthority(authorization(envelope)),
                           risk_port=MemoryRiskGuard(risk(envelope)))
    assert verdict.status == "BLOCKED" and verdict.code == "IDENTITY_BLOCKED"


def test_identity_mismatch_and_stale_time_blocked():
    envelope, gate = gated_envelope()
    twin = sample(observed_value="1.30")
    with pytest.raises(GateError, match="AUTH_IDENTITY_MISMATCH"):
        gate.record_authorization(twin, authorization(envelope))
    late = gate.consume(envelope, authorization_id="auth-1",
                        now=envelope.valid_until,
                        authority_port=MemoryAuthority(authorization(envelope)),
                        risk_port=MemoryRiskGuard(risk(envelope)))
    assert late.status == "BLOCKED" and late.code == "EVIDENCE_NOT_CURRENT"


def test_epoch_monotonic_and_revoke_blocks():
    envelope, gate = gated_envelope()
    gate.revoke_envelope(envelope, epoch=1, now=T + M5)
    with pytest.raises(GateError, match="IDENTITY_BLOCKED"):
        gate.record_authorization(
            envelope, authorization(envelope, epoch=1, auth_id="auth-2"))
    verdict = gate.consume(envelope, authorization_id="auth-1", now=T + M5,
                           authority_port=MemoryAuthority(authorization(envelope)),
                           risk_port=MemoryRiskGuard(risk(envelope)))
    assert verdict.status == "BLOCKED" and verdict.code == "IDENTITY_BLOCKED"
    with pytest.raises(GateError, match="EPOCH_NOT_ADVANCED"):
        gate.revoke_envelope(envelope, epoch=1, now=T + M5)


def test_shadow_decision_and_time_split_reviews():
    envelope = sample()
    ledger = ShadowLedger()
    decision = ShadowDecision(
        shadow_id="shadow-1", event_id=envelope.event_id,
        evidence_version=envelope.evidence_version,
        envelope_hash=envelope.envelope_hash, decision_at=T,
        hypothesis="breakout continuation", planned_action="observe entry",
        invalidation_condition="close below baseline",
        visible_evidence_refs=("raw:1",))
    ledger.append_decision(envelope, decision)
    early = ledger.read_asof(envelope, as_of=T)
    assert len(early) == 1 and early[0].reviews_up_to == ()
    review_late = ShadowReview(
        review_id="sr-1", shadow_id="shadow-1", reviewed_at=T + timedelta(minutes=10),
        posterior_refs=("post:1",), hindsight_note="entry held")
    with pytest.raises(Exception, match="REVIEW_BEFORE_DECISION"):
        ledger.append_review(envelope, ShadowReview(
            review_id="sr-0", shadow_id="shadow-1", reviewed_at=T - timedelta(seconds=1),
            posterior_refs=("post:0",), hindsight_note="impossible"))
    ledger.append_review(envelope, review_late)
    assert ledger.read_asof(envelope, as_of=T)[0].reviews_up_to == ()
    later = ledger.read_asof(envelope, as_of=T + timedelta(minutes=11))
    assert len(later[0].reviews_up_to) == 1
    assert ledger.read_asof(envelope, as_of=T + timedelta(minutes=11),
                            )[0].decision.non_executable is True


def test_shadow_ledger_isolated_from_other_envelopes_and_never_executes():
    envelope = sample()
    other = sample(event_id="ev2")
    ledger = ShadowLedger()
    decision = ShadowDecision(
        shadow_id="shadow-1", event_id=envelope.event_id,
        evidence_version=envelope.evidence_version,
        envelope_hash=envelope.envelope_hash, decision_at=T,
        hypothesis="h", planned_action="p", invalidation_condition="i",
        visible_evidence_refs=("raw:1",))
    ledger.append_decision(envelope, decision)
    assert ledger.read_asof(other, as_of=T) == ()
    with pytest.raises(Exception, match="DECISION_IDENTITY_MISMATCH"):
        ledger.append_decision(other, decision)
    for item in (decision, ledger):
        assert item.trading_permitted is False
        assert item.production_permitted is False
    with pytest.raises(Exception):
        ShadowDecision(
            shadow_id="shadow-2", event_id=envelope.event_id,
            evidence_version=envelope.evidence_version,
            envelope_hash=envelope.envelope_hash, decision_at=T,
            hypothesis="h", planned_action="p", invalidation_condition="i",
            visible_evidence_refs=("raw:1",), non_executable=False)
    assert not hasattr(decision, "order_id")
    assert not hasattr(decision, "fill_id")
    assert not hasattr(decision, "pnl")
    assert not hasattr(decision, "authorization_id")
