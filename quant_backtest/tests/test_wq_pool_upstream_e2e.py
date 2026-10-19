"""End-to-end wiring tests: XuChu watch signal pool -> upstream report ->
quant-sector acceptance -> dual-gate consume.

First integration slice that runs the four already-accepted components
(SignalPool + EvidenceStore, RouteOutbox, AgentUpstreamReporter,
UpstreamInbox + PermissionGate) together as one offline pipeline.
Fully deterministic clock, memory only, no network, no filesystem, no
execution semantics anywhere. No product code is modified here; every
component is imported and used exactly as shipped.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from quant_backtest.quant_sector import (
    AuthorizationScope, HumanAuthorization, PermissionGate, QuantUpstreamReport,
    ResearchReview, RiskDecision, UpstreamInbox,
)
from quant_backtest.agent_bridge.mapping import OutboxBindingAdapter
from quant_backtest.agent_bridge.upstream_ack_authority import UpstreamAckAuthority
from quant_backtest.quant_sector.upstream_inbox import UpstreamInboxError
from quant_backtest.watch_dispatch import EvidenceStore, RouteOutbox, freeze_offline
from quant_backtest.watch_dispatch.agent_reporting import (
    AgentAck, AgentIdentity, AgentReportingError, AgentUpstreamReporter,
)
from quant_backtest.watch_dispatch.route_outbox import (
    ACCEPTED_VERIFIED, OutboxError, UNKNOWN,
)
from quant_backtest.watch_dispatch.signal_pool import (
    AcceptanceReceipt, SignalPool, SignalPoolDraft, SignalPoolError,
    STATUS_ACCEPTED, STATUS_CLAIMED, STATUS_OPEN,
)

# ---------------------------------------------------------------------------
# deterministic clock and shared labels
# ---------------------------------------------------------------------------
T0 = datetime(2026, 10, 19, 2, 0, tzinfo=timezone.utc)
H = "a" * 64
VALID = T0 + timedelta(minutes=30)
ONE = timedelta(seconds=1)
MIN5 = timedelta(minutes=5)
POOL_LEASE = T0 + timedelta(minutes=10)
OUTBOX_LEASE = T0 + timedelta(minutes=10)
CLAIM_AT = T0 + timedelta(seconds=3)
ACCEPT_AT = T0 + timedelta(seconds=4)
CONFIRM_AT = T0 + timedelta(seconds=5)
GATE_NOW = T0 + timedelta(minutes=6)
WATCH_ID = "watch-agent-1"
SECTOR_ID = "quant-sector-agent-1"
ROUTE = "QUANT_UPSTREAM_REPORT"


# ---------------------------------------------------------------------------
# evidence envelope factory (mirrors the shipped unit-test fixtures)
# ---------------------------------------------------------------------------
def envelope(event_id: str = "ev-1", **changes):
    fields = dict(
        schema_version=1, event_id=event_id, evidence_version=1,
        previous_version_hash=None, pool="B", tenant_or_owner_id="owner1",
        market="SH", code="600000", source_membership_id="member1",
        source_revision="rev1", rule_id="r1", rule_version=1,
        comparator="GE", threshold="1.00", unit="ratio", window="5m",
        observed_value="1.10", baseline_value="1.00", raw_ref="raw:1",
        raw_digest=H, source_id="src1", calendar_id="cal1",
        source_as_of=T0, visible_at=T0, detected_at=T0, freeze_at=T0,
        valid_until=VALID, facts=("observed",), hypotheses=("c",),
        supporting_refs=("raw:1",), contradicting_refs=(),
        confirmation_condition="c", invalidation_condition="i")
    fields.update(changes)
    return freeze_offline(**fields)


# ---------------------------------------------------------------------------
# agent identities and the injected directory / ack ports
# ---------------------------------------------------------------------------
def watch_agent(agent_id: str = WATCH_ID, **changes) -> AgentIdentity:
    fields = dict(role="WATCH", registry_ref="registry:watch:1",
                  registered_at=T0 - timedelta(days=1), valid_until=VALID)
    fields.update(changes)
    return AgentIdentity(agent_id=agent_id, **fields)


def sector_agent(agent_id: str = SECTOR_ID, **changes) -> AgentIdentity:
    fields = dict(role="QUANT_SECTOR", registry_ref="registry:quant:1",
                  registered_at=T0 - timedelta(days=1), valid_until=VALID)
    fields.update(changes)
    return AgentIdentity(agent_id=agent_id, **fields)


class MemoryDirectory:
    """Independent directory port backed by a fixed registration list."""

    def __init__(self, *agents: AgentIdentity) -> None:
        self.agents = agents

    def resolve(self, agent_id: str, *, role: str, now: datetime):
        for agent in self.agents:
            if agent.agent_id == agent_id and agent.role == role:
                return agent
        return None


class BindingAckPort:
    """Verifies the ack binds exactly this report and receiving agent."""

    def verify(self, ack, report, now: datetime):
        if (type(ack) is AgentAck
                and ack.report_id == report.report_id
                and ack.receiver_agent_id == report.receiver.agent_id
                and ack.ack_id):
            return ack
        return None


# ---------------------------------------------------------------------------
# cross-package bridges (watch-side and sector-side types are structurally
# mirrored but not interchangeable; the test layer translates field by field)
# ---------------------------------------------------------------------------
def to_sector_report(report) -> QuantUpstreamReport:
    """Watch-side AgentReport -> sector-side QuantUpstreamReport."""
    return QuantUpstreamReport(
        report_id=report.report_id, report_kind=report.report_kind,
        event_id=report.event_id, evidence_hash=report.envelope_hash,
        evidence_version=report.evidence_version,
        sender_agent_id=report.sender.agent_id,
        receiver_agent_id=report.receiver.agent_id,
        created_at=report.created_at)


def to_agent_ack(ack) -> AgentAck:
    """Sector-side UpstreamAck -> watch-side AgentAck (same field shape)."""
    return AgentAck(
        ack_id=ack.ack_id, report_id=ack.report_id,
        receiver_agent_id=ack.receiver_agent_id, acked_at=ack.acked_at,
        verification_source=ack.verification_source)


# ---------------------------------------------------------------------------
# pipeline harness: one shared store/pool/outbox/inbox/reporter wiring
# ---------------------------------------------------------------------------
def pool_draft(signal_id: str, env) -> SignalPoolDraft:
    return SignalPoolDraft(signal_id=signal_id, report_kind="ANOMALY",
                           event_id=env.event_id,
                           evidence_hash=env.envelope_hash,
                           evidence_version=env.evidence_version)


def harness(*envs, producer: AgentIdentity | None = None,
            bind_ack_authority: bool = True) -> SimpleNamespace:
    store = EvidenceStore()
    for env in envs:
        store.freeze(env)
    pool = SignalPool(store)
    inbox = UpstreamInbox()
    outbox = RouteOutbox(
        active_ack_port=UpstreamAckAuthority(inbox) if bind_ack_authority else None)
    producer = producer or watch_agent()
    reporter = AgentUpstreamReporter(producer, MemoryDirectory(sector_agent()),
                                     BindingAckPort())
    return SimpleNamespace(store=store, pool=pool, outbox=outbox, inbox=inbox,
                           reporter=reporter, producer=producer)


def pool_append(h: SimpleNamespace, env, signal_id: str, *,
                now: datetime = T0, producer: AgentIdentity | None = None):
    return h.pool.append(pool_draft(signal_id, env),
                         producer=producer or h.producer,
                         store=h.store, now=now)


def claim_pool(h: SimpleNamespace, signal_id: str, *,
               now: datetime = T0 + ONE, lease_until: datetime = POOL_LEASE):
    return h.pool.claim(signal_id, lease_until=lease_until, now=now)


def report_up(h: SimpleNamespace, env, *, report_id: str = "rpt-1",
              now: datetime = T0 + 2 * ONE):
    report = h.reporter.report(env, h.outbox, report_kind="ANOMALY",
                               receiver_agent_id=SECTOR_ID,
                               report_id=report_id,
                               control_epoch=h.outbox.epoch_floor(env), now=now)
    return report, to_sector_report(report)


def confirm_up(h: SimpleNamespace, env, report, ack, *,
               claim_token: str = "tok-1", claim_at: datetime = CLAIM_AT,
               lease_until: datetime = OUTBOX_LEASE,
               now: datetime = CONFIRM_AT):
    return h.reporter.claim_and_confirm(
        env, h.outbox, report, claim_token=claim_token, claim_at=claim_at,
        lease_until=lease_until, ack=ack, now=now)


def run_accepted_chain(h: SimpleNamespace, env, *, signal_id: str = "sig-1",
                       report_id: str = "rpt-1") -> SimpleNamespace:
    """Drive offline ledgers; pool receipt is legacy caller-injected, untrusted."""
    pool_append(h, env, signal_id)
    claim_pool(h, signal_id)
    report, sector_report = report_up(h, env, report_id=report_id)
    receipt = h.inbox.accept(sector_report, now=ACCEPT_AT)
    attempt, finished = confirm_up(h, env, report, to_agent_ack(receipt.ack))
    entry = h.pool.mark_accepted(
        signal_id, receipt=AcceptanceReceipt(
            route=ROUTE, attempt_id=finished.attempt_id,
            external_ref=receipt.ack.ack_id),
        binding_port=OutboxBindingAdapter(h.outbox, env))
    return SimpleNamespace(report=report, sector_report=sector_report,
                           receipt=receipt, attempt=attempt,
                           finished=finished, entry=entry)


def outbox_intent(outbox: RouteOutbox, env):
    return outbox.get_intent(env, route=ROUTE, target_type="AGENT",
                             target_id=SECTOR_ID)


def outbox_attempts(outbox: RouteOutbox, env):
    return outbox.get_attempts(env, route=ROUTE, target_type="AGENT",
                               target_id=SECTOR_ID)


# ---------------------------------------------------------------------------
# dual-gate fixtures (injected authority / risk ports, as shipped tests do)
# ---------------------------------------------------------------------------
def gate_review(env, *, review_id: str = "rev-1") -> ResearchReview:
    return ResearchReview(
        review_id=review_id, event_id=env.event_id,
        evidence_version=env.evidence_version,
        envelope_hash=env.envelope_hash, reviewer_external_identity="analyst-1",
        decision="CONFIRMED", recorded_at=T0)


def gate_scope(env) -> AuthorizationScope:
    return AuthorizationScope(market=env.market, code=env.code, side="BUY",
                              max_quantity=100, sim_only=True)


def gate_authorization(env, *, auth_id: str = "auth-1",
                       review_id: str = "rev-1") -> HumanAuthorization:
    return HumanAuthorization(
        authorization_id=auth_id, event_id=env.event_id,
        evidence_version=env.evidence_version,
        envelope_hash=env.envelope_hash, principal_id="trader-1",
        external_authority_ref="authority:1", auth_epoch=0,
        review_id=review_id, scope=gate_scope(env), issued_at=T0,
        valid_from=T0, valid_until=VALID - MIN5)


def gate_risk(env, *, auth_id: str = "auth-1") -> RiskDecision:
    return RiskDecision(
        decision_id="risk-1", event_id=env.event_id,
        evidence_version=env.evidence_version,
        envelope_hash=env.envelope_hash, authorization_id=auth_id,
        decision="PASS", policy_version="rg-1", as_of=T0 + ONE,
        valid_until=VALID - MIN5, external_verification_ref="rg-verify-1")


class MemoryAuthority:
    def __init__(self, auth: HumanAuthorization) -> None:
        self.auth = auth

    def resolve_and_verify(self, authorization_id, envelope, now):
        if authorization_id != self.auth.authorization_id:
            return None
        return self.auth


class MemoryRiskGuard:
    def __init__(self, decision: RiskDecision) -> None:
        self.decision = decision

    def evaluate_independently(self, envelope, authorization, now):
        if authorization.authorization_id != self.decision.authorization_id:
            return None
        return self.decision


def wire_gate(h: SimpleNamespace, env) -> PermissionGate:
    """PermissionGate whose acceptance lookup queries the live inbox ledger."""
    gate = PermissionGate(acceptance_lookup=h.inbox.is_active)
    assert gate.submit_review(env, gate_review(env)) == "REVIEW_CONFIRMED"
    gate.record_authorization(env, gate_authorization(env))
    return gate


# ---------------------------------------------------------------------------
# 1. Offline ledger alignment only; no production acceptance authorization
# ---------------------------------------------------------------------------
def test_full_chain_happy_path_three_ledgers_align():
    env = envelope()
    h = harness(env)
    r = run_accepted_chain(h, env)

    # Caller-injected legacy pool projection is NOT a trusted acceptance root.
    assert h.pool.acceptance_mode == "LEGACY_UNTRUSTED"
    assert h.pool.production_permitted is False
    assert h.pool.notification_permitted is False
    assert h.pool.trading_permitted is False
    assert r.entry.status == STATUS_ACCEPTED
    assert r.entry.notification_permitted is False
    assert r.entry.trading_permitted is False
    assert h.pool.get("sig-1").status == STATUS_ACCEPTED
    pool_receipt = h.pool.receipt("sig-1")
    assert pool_receipt.route == ROUTE
    assert pool_receipt.attempt_id == r.finished.attempt_id
    assert pool_receipt.external_ref == r.receipt.ack.ack_id
    assert h.pool.list_open(CONFIRM_AT) == ()

    # Outbox ledger: intent and attempt both settled ACCEPTED_VERIFIED.
    intent = outbox_intent(h.outbox, env)
    assert intent is not None and intent.status == ACCEPTED_VERIFIED
    attempts = outbox_attempts(h.outbox, env)
    assert len(attempts) == 1
    assert attempts[0].outcome == ACCEPTED_VERIFIED
    assert attempts[0].receipt is not None

    # Inbox ledger: exactly one active receipt for the report.
    assert h.inbox.receipt_for_report("rpt-1") is r.receipt
    assert h.inbox.is_active(r.receipt.receipt_id)
    assert len(h.inbox.list_accepted()) == 1


def test_outbox_receipt_binds_inbox_ack_and_pool_receipt():
    env = envelope()
    h = harness(env)
    r = run_accepted_chain(h, env)

    attempt = outbox_attempts(h.outbox, env)[0]
    receipt = attempt.receipt
    # The outbox receipt references the sector inbox ack verbatim.
    assert receipt.external_message_id == r.receipt.ack.ack_id
    assert receipt.verification_source == r.receipt.ack.verification_source
    assert receipt.verification_source == "upstream-inbox-v1"
    assert receipt.observed_at == r.receipt.ack.acked_at
    assert receipt.route == ROUTE
    assert receipt.attempt_id == r.finished.attempt_id
    # And the pool receipt references the same attempt and ack.
    pool_receipt = h.pool.receipt("sig-1")
    assert pool_receipt.attempt_id == receipt.attempt_id
    assert pool_receipt.external_ref == receipt.external_message_id


# ---------------------------------------------------------------------------
# 2. UNKNOWN path: un-verified ack keeps the pool claimed; lease recycle
#    reopens the pool entry, but the outbox intent cannot be re-driven
# ---------------------------------------------------------------------------
def test_mismatched_ack_lands_unknown_and_pool_stays_claimed():
    env = envelope()
    h = harness(env)
    pool_append(h, env, "sig-1")
    claim_pool(h, "sig-1")
    report, sector_report = report_up(h, env)
    receipt = h.inbox.accept(sector_report, now=ACCEPT_AT)

    # Ack bound to a different report: the ack port refuses to verify it.
    stranger = AgentAck(ack_id="ack-stranger", report_id="rpt-other",
                        receiver_agent_id=SECTOR_ID, acked_at=ACCEPT_AT,
                        verification_source="upstream-inbox-v1")
    assert BindingAckPort().verify(stranger, report, CONFIRM_AT) is None
    attempt, finished = confirm_up(h, env, report, stranger)
    assert finished.outcome == UNKNOWN
    assert finished.receipt is None
    assert outbox_intent(h.outbox, env).status == UNKNOWN
    # The pool entry stays CLAIMED while its lease still holds.
    assert h.pool.get("sig-1", now=CONFIRM_AT).status == STATUS_CLAIMED
    assert h.pool.receipt("sig-1") is None


def test_expired_pool_lease_recycles_and_live_original_ack_can_settle_retry():
    """An active original-report ACK can settle a retry, not authorize the pool."""
    env = envelope()
    h = harness(env)
    pool_append(h, env, "sig-1")
    claim_pool(h, "sig-1")
    report, sector_report = report_up(h, env)
    receipt = h.inbox.accept(sector_report, now=ACCEPT_AT)
    wrong_ack = AgentAck(ack_id="ack-stranger", report_id="rpt-other",
                         receiver_agent_id=SECTOR_ID, acked_at=ACCEPT_AT,
                         verification_source="upstream-inbox-v1")
    confirm_up(h, env, report, wrong_ack)
    assert outbox_intent(h.outbox, env).status == UNKNOWN

    # Advance now past the pool lease: lazy recycle returns the entry to OPEN.
    recycle_at = T0 + timedelta(minutes=11)
    open_entries = h.pool.list_open(recycle_at)
    assert [e.status for e in open_entries] == [STATUS_OPEN]
    reclaimed = claim_pool(h, "sig-1", now=recycle_at,
                           lease_until=recycle_at + timedelta(minutes=10))
    assert reclaimed.status == STATUS_CLAIMED

    next_report = h.reporter.retry_report(
        env, h.outbox, report, generation=1,
        control_epoch=h.outbox.epoch_floor(env), now=recycle_at)
    assert next_report is report
    assert next_report.report_id == report.report_id
    assert next_report.created_at == sector_report.created_at
    assert outbox_intent(h.outbox, env).intent_id == "report-rpt-1-g1"
    assert h.inbox.accept(to_sector_report(next_report),
                          now=recycle_at + ONE) is receipt
    # The original report ACK predates the retry intent, but remains active in
    # the authoritative inbox. Only this original-report retry may reuse it.
    attempt, finished = confirm_up(h, env, next_report, to_agent_ack(receipt.ack),
                                   claim_token="tok-2", claim_at=recycle_at,
                                   lease_until=recycle_at + timedelta(minutes=10),
                                   now=recycle_at + ONE)
    assert attempt.attempt_no == 2
    assert finished.outcome == ACCEPTED_VERIFIED
    assert finished.receipt.external_message_id == receipt.ack.ack_id
    assert h.pool.get("sig-1").status == STATUS_CLAIMED
    assert h.pool.receipt("sig-1") is None
    assert h.pool.production_permitted is False


def test_revoked_sector_ack_cannot_settle_new_generation():
    """A revoked sector receipt remains unusable even without outbox revocation."""
    env = envelope()
    h = harness(env)
    report, sector_report = report_up(h, env)
    receipt = h.inbox.accept(sector_report, now=ACCEPT_AT)
    wrong_ack = AgentAck(ack_id="ack-stranger", report_id="rpt-other",
                         receiver_agent_id=SECTOR_ID, acked_at=ACCEPT_AT,
                         verification_source="upstream-inbox-v1")
    confirm_up(h, env, report, wrong_ack)
    retry = h.reporter.retry_report(
        env, h.outbox, report, generation=1,
        control_epoch=h.outbox.epoch_floor(env), now=T0 + timedelta(minutes=6))
    h.inbox.revoke(receipt.receipt_id, now=T0 + timedelta(minutes=7))
    assert h.inbox.is_active(receipt.receipt_id) is False
    with pytest.raises(UpstreamInboxError, match="RECEIPT_REVOKED"):
        h.inbox.accept(to_sector_report(retry), now=T0 + timedelta(minutes=8))
    old_ack = to_agent_ack(receipt.ack)
    _, finished = confirm_up(
        h, env, retry, old_ack, claim_token="tok-2",
        claim_at=T0 + timedelta(minutes=8),
        lease_until=T0 + timedelta(minutes=12),
        now=T0 + timedelta(minutes=8, seconds=1))
    assert finished.outcome == UNKNOWN and finished.receipt is None


def test_unknown_signal_does_not_poison_pipeline_for_next_signal():
    """A wedged UNKNOWN intent on ev-1 does not block a fresh event from
    running the same wiring end to end and settling ACCEPTED."""
    env1, env2 = envelope("ev-1"), envelope("ev-2")
    h = harness(env1, env2)

    # ev-1: full wiring, ack verification refused -> UNKNOWN, intent wedged.
    pool_append(h, env1, "sig-1")
    claim_pool(h, "sig-1")
    report1, sector_report1 = report_up(h, env1, report_id="rpt-1")
    h.inbox.accept(sector_report1, now=ACCEPT_AT)
    wrong_ack = AgentAck(ack_id="ack-stranger", report_id="rpt-other",
                         receiver_agent_id=SECTOR_ID, acked_at=ACCEPT_AT,
                         verification_source="upstream-inbox-v1")
    _, finished1 = confirm_up(h, env1, report1, wrong_ack)
    assert finished1.outcome == UNKNOWN

    # ev-2: same store, pool, outbox, inbox, reporter; full loop settles.
    r2 = run_accepted_chain(h, env2, signal_id="sig-2", report_id="rpt-2")
    assert r2.entry.status == STATUS_ACCEPTED
    assert r2.finished.outcome == ACCEPTED_VERIFIED
    assert outbox_intent(h.outbox, env2).status == ACCEPTED_VERIFIED
    assert outbox_intent(h.outbox, env1).status == UNKNOWN
    assert h.inbox.is_active(r2.receipt.receipt_id)
    assert h.pool.receipt("sig-2").external_ref == r2.receipt.ack.ack_id
    statuses = {e.signal_id: e.status
                for e in (h.pool.get("sig-1"), h.pool.get("sig-2"))}
    assert statuses["sig-2"] == STATUS_ACCEPTED
    assert statuses["sig-1"] == STATUS_CLAIMED


def test_verified_ack_before_outbox_claim_is_accepted():
    """An active inbox ACK after intent creation can precede outbox claim."""
    env = envelope()
    h = harness(env)
    pool_append(h, env, "sig-1")
    claim_pool(h, "sig-1")
    report, sector_report = report_up(h, env, now=T0 + 2 * ONE)
    # Sector accepts AFTER report/intent creation, BEFORE outbox claim.
    receipt = h.inbox.accept(sector_report, now=CLAIM_AT - ONE / 2)
    agent_ack = to_agent_ack(receipt.ack)
    # The ack itself is fully verified by the independent port...
    assert BindingAckPort().verify(agent_ack, report, CONFIRM_AT) is agent_ack
    # The outbox preserves it because it postdates report creation.
    attempt, finished = confirm_up(h, env, report, agent_ack)
    assert finished.outcome == ACCEPTED_VERIFIED
    assert finished.receipt is not None
    assert finished.receipt.external_message_id == receipt.ack.ack_id
    assert outbox_intent(h.outbox, env).status == ACCEPTED_VERIFIED
    assert h.pool.get("sig-1", now=CONFIRM_AT).status == STATUS_CLAIMED


def test_revoked_active_ack_degrades_to_unknown_before_outbox_finish():
    """A valid historical ACK cannot settle after inbox revocation."""
    env = envelope()
    h = harness(env)
    report, sector_report = report_up(h, env)
    receipt = h.inbox.accept(sector_report, now=ACCEPT_AT)
    h.inbox.revoke(receipt.receipt_id, now=ACCEPT_AT + ONE / 2)
    assert h.inbox.is_active(receipt.receipt_id) is False
    assert BindingAckPort().verify(to_agent_ack(receipt.ack), report,
                                   CONFIRM_AT) is not None
    _, finished = confirm_up(h, env, report, to_agent_ack(receipt.ack))
    assert finished.outcome == UNKNOWN
    assert finished.receipt is None
    assert outbox_intent(h.outbox, env).status == UNKNOWN


def test_unbound_ack_authority_degrades_valid_ack_to_unknown():
    """Matching fields cannot replace constructor-bound inbox authority."""
    env = envelope()
    h = harness(env, bind_ack_authority=False)
    report, sector_report = report_up(h, env)
    receipt = h.inbox.accept(sector_report, now=ACCEPT_AT)
    assert h.inbox.is_active(receipt.receipt_id)
    assert BindingAckPort().verify(to_agent_ack(receipt.ack), report,
                                   CONFIRM_AT) is not None
    _, finished = confirm_up(h, env, report, to_agent_ack(receipt.ack))
    assert finished.outcome == UNKNOWN
    assert finished.receipt is None
    assert outbox_intent(h.outbox, env).status == UNKNOWN


def test_future_verified_ack_still_degrades_to_unknown():
    env = envelope()
    h = harness(env)
    report, sector_report = report_up(h, env)
    receipt = h.inbox.accept(sector_report, now=ACCEPT_AT)
    forged = AgentAck(ack_id=receipt.ack.ack_id, report_id=report.report_id,
                      receiver_agent_id=SECTOR_ID,
                      acked_at=CONFIRM_AT + ONE,
                      verification_source=receipt.ack.verification_source)
    _, finished = confirm_up(h, env, report, forged)
    assert finished.outcome == UNKNOWN
    assert finished.receipt is None


# ---------------------------------------------------------------------------
# 3. dual gate: acceptance precondition runs before both gates
# ---------------------------------------------------------------------------
def test_dual_gate_blocks_consume_before_upstream_acceptance():
    env = envelope()
    h = harness(env)
    pool_append(h, env, "sig-1")
    claim_pool(h, "sig-1")
    report_up(h, env)  # planned but never accepted by the sector inbox
    gate = wire_gate(h, env)

    blocked = gate.consume(
        env, authorization_id="auth-1", now=GATE_NOW,
        authority_port=MemoryAuthority(gate_authorization(env)),
        risk_port=MemoryRiskGuard(gate_risk(env)), acceptance_ref=None)
    assert blocked.status == "BLOCKED"
    assert blocked.code == "UPSTREAM_NOT_ACCEPTED"

    unknown_ref = gate.consume(
        env, authorization_id="auth-1", now=GATE_NOW,
        authority_port=MemoryAuthority(gate_authorization(env)),
        risk_port=MemoryRiskGuard(gate_risk(env)),
        acceptance_ref="receipt-never-issued")
    assert unknown_ref.status == "BLOCKED"
    assert unknown_ref.code == "UPSTREAM_NOT_ACCEPTED"


def test_dual_gate_passes_with_active_acceptance():
    env = envelope()
    h = harness(env)
    r = run_accepted_chain(h, env)
    gate = wire_gate(h, env)

    verdict = gate.consume(
        env, authorization_id="auth-1", now=GATE_NOW,
        authority_port=MemoryAuthority(gate_authorization(env)),
        risk_port=MemoryRiskGuard(gate_risk(env)),
        acceptance_ref=r.receipt.receipt_id)
    assert verdict.status == "SIM_DISPATCH_ELIGIBLE"
    assert verdict.code == "GATES_PASS"
    assert verdict.authorization.authorization_id == "auth-1"
    assert verdict.risk.decision == "PASS"
    # Even a fully eligible verdict cannot itself open the disabled port.
    assert verdict.dispatch_permitted is False
    assert verdict.trading_permitted is False


def test_dual_gate_reblocks_after_acceptance_revocation():
    env = envelope()
    h = harness(env)
    r = run_accepted_chain(h, env)
    gate = wire_gate(h, env)
    ok = gate.consume(
        env, authorization_id="auth-1", now=GATE_NOW,
        authority_port=MemoryAuthority(gate_authorization(env)),
        risk_port=MemoryRiskGuard(gate_risk(env)),
        acceptance_ref=r.receipt.receipt_id)
    assert ok.status == "SIM_DISPATCH_ELIGIBLE"

    # Revoking the sector-side acceptance closes the precondition again.
    h.inbox.revoke(r.receipt.receipt_id, now=T0 + timedelta(minutes=7))
    assert h.inbox.is_active(r.receipt.receipt_id) is False
    reblocked = gate.consume(
        env, authorization_id="auth-1", now=T0 + timedelta(minutes=8),
        authority_port=MemoryAuthority(gate_authorization(env)),
        risk_port=MemoryRiskGuard(gate_risk(env)),
        acceptance_ref=r.receipt.receipt_id)
    assert reblocked.status == "BLOCKED"
    assert reblocked.code == "UPSTREAM_NOT_ACCEPTED"
    # And the revoked report can never be re-accepted.
    with pytest.raises(UpstreamInboxError, match="RECEIPT_REVOKED"):
        h.inbox.accept(r.sector_report, now=T0 + timedelta(minutes=8))


# ---------------------------------------------------------------------------
# 4. idempotency and conflict behaviour across the wiring
# ---------------------------------------------------------------------------
def test_inbox_accept_is_idempotent_same_receipt():
    env = envelope()
    h = harness(env)
    r = run_accepted_chain(h, env)
    again = h.inbox.accept(r.sector_report, now=GATE_NOW)
    assert again is r.receipt
    assert h.inbox.ledger_epoch == 0
    assert len(h.inbox.list_accepted()) == 1
    # Outbox ledger untouched by the replay.
    assert len(outbox_attempts(h.outbox, env)) == 1


def test_pool_reappend_same_event_hash_producer_is_idempotent():
    env = envelope()
    h = harness(env)
    first = pool_append(h, env, "sig-1")
    again = pool_append(h, env, "sig-again", now=T0 + ONE)
    assert again is first
    assert h.pool.list_open(T0 + ONE) == (first,)
    # No upstream side effects from the deduplicated replay.
    assert outbox_intent(h.outbox, env) is None
    assert len(h.inbox.list_accepted()) == 0


# ---------------------------------------------------------------------------
# 5. identity discipline enforced at the pool mouth and the report mouth
# ---------------------------------------------------------------------------
def test_non_watch_producer_rejected_at_pool_mouth():
    env = envelope()
    h = harness(env)
    with pytest.raises(SignalPoolError, match="PRODUCER_ROLE_INVALID"):
        pool_append(h, env, "sig-1", producer=sector_agent())
    # Nothing leaked into any of the three ledgers.
    assert h.pool.get("sig-1") is None
    assert h.pool.list_open(T0) == ()
    assert outbox_intent(h.outbox, env) is None
    assert len(h.inbox.list_accepted()) == 0


def test_expired_producer_rejected_at_pool_and_report_mouths():
    env = envelope()
    # Pool mouth: a producer whose identity lapsed before now is refused.
    stale = watch_agent(valid_until=T0)
    h = harness(env, producer=stale)
    with pytest.raises(SignalPoolError, match="AGENT_NOT_CURRENT"):
        pool_append(h, env, "sig-1")
    assert h.pool.get("sig-1") is None

    # Report mouth: sender current at pool time but lapsed at report time.
    short_lived = watch_agent(valid_until=T0 + 2 * ONE)
    h2 = harness(env, producer=short_lived)
    pool_append(h2, env, "sig-1")  # current at T0
    claim_pool(h2, "sig-1")
    with pytest.raises(AgentReportingError, match="SENDER_NOT_CURRENT"):
        report_up(h2, env, now=T0 + 3 * ONE)
    # The intent was never planned: the outbox stays empty.
    assert outbox_intent(h2.outbox, env) is None


def test_reporter_rejects_non_watch_sender_at_construction():
    with pytest.raises(AgentReportingError, match="SENDER_MUST_BE_WATCH_AGENT"):
        AgentUpstreamReporter(sector_agent(), MemoryDirectory(sector_agent()),
                              BindingAckPort())
