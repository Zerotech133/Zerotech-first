"""End-to-end offline chain test for the financial-game offline slice.

One synthetic, fully offline pass through the real modules:

    StockPoolSource.publish -> select -> frozen EvidenceEnvelope ->
    prepare_trigger draft -> real SignalPool.append -> real SignalPoolEntry ->
    real UpstreamInbox.accept -> real UpstreamReceipt -> InternalSignalFanout
    (plan/claim/finish/consume through four injected trusted ports) ->
    ResearchPlanBook (plan publication + historical attribution + review) ->
    CurrentEligibility.evaluate (caller-supplied AuthorityRoot) ->
    signal_ref_bridge.build_signal_ref / build_acceptance_ref + verify.

Every fixture is synthetic and in-memory. The real modules are invoked for
real (no internal mocking); nothing here touches production, notifications,
trading or the network. Key adversarial negatives covered:

- a revoked authority root is NOT_ELIGIBLE (never FAIL_CLOSED-masked);
- HEALTH reports are accepted upstream but never enter the business fanout
  or signal-reference path;
- forged / impostor reports are refused by the fanout's cross-binding gates;
- a plan published in the future is invisible to earlier as-of reads
  (no lookahead) and current eligibility is re-derived, never inherited.

The bridge digests are asserted to be audit references only:
``is_permission`` is always False and ``permission_token`` always refuses.
Determinism (identical arguments -> identical outputs) and zero production
side effects (real ledger state snapshots unchanged across pure reads) are
asserted explicitly.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from quant_backtest.quant_sector.current_eligibility import (
    CURRENT_ELIGIBLE,
    NOT_ELIGIBLE,
    FAIL_CLOSED,
    AuthorityRoot,
    EligibilityVerdict,
    evaluate,
)
from quant_backtest.quant_sector.internal_signal_fanout import (
    InternalFanoutError,
    InternalSignalFanout,
    RESEARCH,
    TRADING,
)
from quant_backtest.quant_sector.research_plan_book import (
    PlanRef,
    ResearchPlan,
    ResearchPlanBook,
    ResearchReviewFact,
    ResearchSignal,
)
from quant_backtest.quant_sector.signal_ref_bridge import (
    SignalRefBridgeError,
    build_acceptance_ref,
    build_signal_ref,
    verify_acceptance_ref,
    verify_signal_ref,
)
from quant_backtest.quant_sector.upstream_inbox import (
    QuantUpstreamReport,
    UpstreamInbox,
    UpstreamInboxError,
)
from quant_backtest.watch_dispatch.agent_reporting import AgentIdentity
from quant_backtest.watch_dispatch.contracts import freeze_offline
from quant_backtest.watch_dispatch.evidence_store import EvidenceStore
from quant_backtest.watch_dispatch.signal_pool import (
    SignalPool,
    SignalPoolDraft,
    STATUS_OPEN,
)
from quant_backtest.watch_dispatch.stock_pool_source import (
    OFFLINE_SHADOW,
    PoolMember,
    StockPoolSnapshot,
    StockPoolSource,
)

T = datetime(2026, 10, 20, 2, 0, tzinfo=timezone.utc)
MIN = timedelta(minutes=1)
POOL_ID = "game-e2e-pool"
PUBLISHER = "game-publisher"
MEMBER_ID = "game-member-1"
MEMBER = PoolMember("SH", "600000", MEMBER_ID)
WATCHER_ID = "watch-agent-1"
RECEIVER_ID = "quant-agent-1"
SIGNAL_ID = "game-signal-1"
EVENT_ID = "game-event-1"
REPORT_ID = "game-report-1"
PLAN_ID = "game-plan-1"
PROFILE = "game-conservative"
PROFILE_VERSION = "v1"
CONSUMERS = frozenset({"researcher-1", "trader-1"})


def at(minutes: int) -> datetime:
    """Canonical timeline helper anchored at T."""
    return T + timedelta(minutes=minutes)


def watcher(*, valid_until: datetime = at(60)) -> AgentIdentity:
    """Current WATCH producer identity for the whole slice window."""
    return AgentIdentity(WATCHER_ID, "WATCH", "game-registry", T - MIN,
                         valid_until)


def snapshot(*, version: int = 1, published_at: datetime = T,
             visible_at: datetime = T + MIN,
             valid_until: datetime = at(45)) -> StockPoolSnapshot:
    """One offline-shadow pool version pinned to the synthetic member."""
    return StockPoolSnapshot(
        pool_id=POOL_ID, version=version, publisher=PUBLISHER,
        members=(MEMBER,), published_at=published_at, visible_at=visible_at,
        valid_until=valid_until, source_manifest="a" * 64,
        status=OFFLINE_SHADOW)


def envelope(*, event_id: str = EVENT_ID, member: str = MEMBER_ID,
             freeze: datetime = T + 2 * MIN,
             valid_until: datetime = at(30)) -> object:
    """Self-consistent frozen observation envelope (never a real source)."""
    return freeze_offline(
        schema_version=1, evidence_version=1, previous_version_hash=None,
        pool="B", tenant_or_owner_id="game-owner", market="SH", code="600000",
        source_membership_id=member, source_revision="game-revision-1",
        rule_id="game-rule-1", rule_version=1, comparator="GE",
        threshold="1.00", unit="ratio", window="5m", observed_value="1.10",
        baseline_value="1.00", raw_ref="raw:game", raw_digest="b" * 64,
        source_id="game-source", calendar_id="game-calendar",
        source_as_of=freeze, visible_at=freeze, detected_at=freeze,
        freeze_at=freeze, valid_until=valid_until, facts=("synthetic-fact",),
        hypotheses=("synthetic-hypothesis",),
        supporting_refs=("raw:game",), contradicting_refs=(),
        confirmation_condition="synthetic confirm",
        invalidation_condition="synthetic invalidate", event_id=event_id)


def chain():
    """Run the real offline chain up to the fanout/plan-book/bridge inputs.

    Returns every intermediate artifact so each test can assert on the
    exact stage it exercises. All objects are real module instances; the
    only caller-supplied authority (consumer allowlist) is the local test
    configuration demanded by the fanout contract.
    """
    store = EvidenceStore()
    proof = envelope()
    store.freeze(proof)

    source = StockPoolSource(frozenset({PUBLISHER}))
    source.publish(snapshot(), now=T)
    selection = source.select(POOL_ID, market="SH", symbol="600000",
                              watcher=watcher(), as_of=T + 3 * MIN)
    intent = source.prepare_trigger(
        selection, triggered=True, evidence=proof, store=store,
        signal_id=SIGNAL_ID, trigger_at=T + 3 * MIN, now=T + 3 * MIN)

    pool = SignalPool(store)
    entry = pool.append(intent.draft, producer=watcher(), store=store,
                        now=T + 4 * MIN)

    inbox = UpstreamInbox()
    report = QuantUpstreamReport(
        report_id=REPORT_ID, report_kind="ANOMALY", event_id=EVENT_ID,
        evidence_hash=proof.envelope_hash, evidence_version=1,
        sender_agent_id=WATCHER_ID, receiver_agent_id=RECEIVER_ID,
        created_at=T + 5 * MIN)
    receipt = inbox.accept(report, now=T + 5 * MIN)

    def inbox_lookup(report_id: str):
        """Trusted receipt-state lookup bound to the real inbox.

        Combines ``receipt_for_report`` with ``is_revoked`` exactly as the
        bridge contract prescribes, so a revoked receipt can never pass.
        """
        current = inbox.receipt_for_report(report_id)
        if current is None or inbox.is_revoked(report_id):
            return None
        return current

    fanout = InternalSignalFanout(
        acceptance_lookup=inbox.get,
        acceptance_is_active=inbox.is_active,
        signal_lookup=lambda signal_id, now: pool.get(signal_id, now=now),
        consumer_authorized=lambda team, identity, now: identity in CONSUMERS)

    book = ResearchPlanBook()
    plan = book.publish(ResearchPlan(
        ref=PlanRef(PLAN_ID, 1), market="SH", symbol="600000", side="BUY",
        thesis="synthetic game hypothesis",
        entry_condition="synthetic entry evidence",
        exit_condition="synthetic exit evidence", profile_id=PROFILE,
        profile_version=PROFILE_VERSION,
        risk_budget_description="abstract offline risk template",
        published_at=T, effective_from=T + MIN, effective_until=at(40),
        approver_id="game-approver"))

    return {
        "store": store, "proof": proof, "source": source,
        "selection": selection, "intent": intent, "pool": pool,
        "entry": entry, "inbox": inbox, "report": report,
        "receipt": receipt, "inbox_lookup": inbox_lookup,
        "fanout": fanout, "book": book, "plan": plan,
    }


def consume_both(fanout: InternalSignalFanout, *, report: QuantUpstreamReport,
                 receipt_id: str, signal_id: str) -> None:
    """Drive plan/claim/finish/consume for both internal teams."""
    fanout.fanout(report=report, receipt_id=receipt_id, signal_id=signal_id,
                  research_identity="researcher-1",
                  trading_identity="trader-1", now=T + 6 * MIN)
    for team, reply in ((RESEARCH, "RESEARCH_INTENT"),
                        (TRADING, "TRADER_OBSERVE")):
        identity = "researcher-1" if team == RESEARCH else "trader-1"
        claim = fanout.claim(signal_id=signal_id, team=team,
                             consumer_identity=identity, now=T + 7 * MIN,
                             lease_until=at(25))
        response = fanout.finish(signal_id=signal_id, team=team,
                                 consumer_identity=identity, claim=claim,
                                 outcome="LOGICAL_RECEIVED",
                                 now=T + 8 * MIN, reply=reply)
        assert response.outcome == "LOGICAL_RECEIVED"
        seen = fanout.consume(signal_id=signal_id, team=team,
                              consumer_identity=identity, now=T + 9 * MIN)
        assert seen is not None and seen.reply == reply


def active_root() -> AuthorityRoot:
    """Caller-supplied active authority root bound to the chain identity."""
    return AuthorityRoot(signal_id=SIGNAL_ID, event_id=EVENT_ID,
                         status="ACTIVE", effective_from=T + MIN,
                         effective_until=at(40), market="SH", symbol="600000")


# ----------------------------------------------------------------------
# Positive: the full offline chain, end to end
# ----------------------------------------------------------------------
def test_full_chain_positive_end_to_end() -> None:
    ctx = chain()

    # G1: draft adapter produced a real, non-executable intent.
    intent = ctx["intent"]
    assert intent.draft.signal_id == SIGNAL_ID
    assert intent.draft.event_id == EVENT_ID
    assert intent.draft.evidence_hash == ctx["proof"].envelope_hash
    assert intent.pool_id == POOL_ID and intent.pool_version == 1
    assert intent.producer_agent_id == WATCHER_ID
    assert intent.status == "OFFLINE_DRAFT_ONLY"
    assert not intent.production_permitted and not intent.notification_permitted
    assert not intent.trading_permitted

    # Real SignalPool.append produced a real OPEN entry bound to the frozen
    # evidence identity and the verified producer attribution.
    entry = ctx["entry"]
    assert entry.signal_id == SIGNAL_ID
    assert entry.producer_agent_id == WATCHER_ID
    assert entry.report_kind == "ANOMALY"
    assert entry.status == STATUS_OPEN and entry.epoch == 0
    assert (entry.event_id, entry.evidence_hash, entry.evidence_version) == (
        EVENT_ID, ctx["proof"].envelope_hash, 1)

    # Real UpstreamInbox.accept issued a locally verified receipt + ACK.
    receipt = ctx["receipt"]
    assert receipt.report_id == REPORT_ID
    assert receipt.envelope_hash == ctx["report"].envelope_hash
    assert receipt.ack.report_id == REPORT_ID
    assert receipt.ack.receiver_agent_id == RECEIVER_ID
    assert receipt.ack.verification_source == "upstream-inbox-v1"
    assert receipt.ack.acked_at >= receipt.accepted_at
    assert receipt.ledger_epoch == 0
    assert ctx["inbox"].is_active(receipt.receipt_id)

    # Internal fanout: both teams plan/claim/finish/consume independently.
    consume_both(ctx["fanout"], report=ctx["report"],
                 receipt_id=receipt.receipt_id, signal_id=SIGNAL_ID)
    research = ctx["fanout"].consume(signal_id=SIGNAL_ID, team=RESEARCH,
                                     consumer_identity="researcher-1",
                                     now=T + 10 * MIN)
    trading = ctx["fanout"].consume(signal_id=SIGNAL_ID, team=TRADING,
                                    consumer_identity="trader-1",
                                    now=T + 10 * MIN)
    assert research is not None and research.team == RESEARCH
    assert trading is not None and trading.team == TRADING
    assert research.subscription_id != trading.subscription_id
    assert ctx["fanout"].status(signal_id=SIGNAL_ID, team=RESEARCH,
                                consumer_identity="researcher-1",
                                now=T + 10 * MIN) == "LOGICAL_RECEIVED"

    # Research plan book: signal-time attribution frozen, review appended.
    book = ctx["book"]
    signal = ResearchSignal(
        signal_id=SIGNAL_ID, market="SH", symbol="600000", side="BUY",
        profile_id=PROFILE, profile_version=PROFILE_VERSION,
        triggered_at=intent.trigger_at, observed_at=T + 10 * MIN)
    match = book.match_signal(signal)
    assert match.status == "MATCHED"
    assert match.plan_ref == PlanRef(PLAN_ID, 1)
    assert not match.trading_permitted and not match.notification_permitted
    book.review(ResearchReviewFact(
        review_id="game-review-1", signal_id=SIGNAL_ID,
        decision="CONFIRMED", reason="synthetic offline confirmation",
        recorded_at=T + 11 * MIN, plan_ref=PlanRef(PLAN_ID, 1)))
    assert book.signal_match(SIGNAL_ID) is match
    assert tuple(r.review_id for r in book.reviews_for(SIGNAL_ID)) == (
        "game-review-1",)

    # Current eligibility: caller-supplied active root + as-of plan state.
    verdict = evaluate(book, active_root(), "SH", "600000",
                       as_of=T + 12 * MIN)
    assert isinstance(verdict, EligibilityVerdict)
    assert verdict.status == CURRENT_ELIGIBLE
    assert verdict.reason == "CURRENT_PLAN_ACTIVE"
    assert verdict.evidence_refs == (
        "authority:" + EVENT_ID, "signal:" + SIGNAL_ID,
        "plan:" + PLAN_ID + ":1")
    assert verdict.is_current_eligible
    assert not verdict.is_fail_closed
    assert not verdict.trading_permitted and not verdict.notification_permitted

    # Bridge: both audit references build from real objects and verify.
    signal_ref = build_signal_ref(intent, entry)
    acceptance_ref = build_acceptance_ref(
        ctx["report"], receipt, inbox_epoch=ctx["inbox"].ledger_epoch,
        inbox_receipt_lookup=ctx["inbox_lookup"])
    assert verify_signal_ref(signal_ref, intent, entry) is signal_ref
    assert verify_acceptance_ref(
        acceptance_ref, ctx["report"], receipt,
        inbox_epoch=ctx["inbox"].ledger_epoch,
        inbox_receipt_lookup=ctx["inbox_lookup"]) is acceptance_ref
    assert signal_ref.pool_id == POOL_ID and signal_ref.pool_version == 1
    assert signal_ref.membership_id == MEMBER_ID
    assert acceptance_ref.receipt_id == receipt.receipt_id
    assert acceptance_ref.ack_id == receipt.ack.ack_id


# ----------------------------------------------------------------------
# Negative 1: revoked authority root is NOT_ELIGIBLE, never fail-closed
# ----------------------------------------------------------------------
def test_revoked_authority_root_is_not_eligible() -> None:
    ctx = chain()
    book = ctx["book"]
    book.match_signal(ResearchSignal(
        signal_id=SIGNAL_ID, market="SH", symbol="600000", side="BUY",
        profile_id=PROFILE, profile_version=PROFILE_VERSION,
        triggered_at=T + 3 * MIN, observed_at=T + 10 * MIN))

    revoked = replace(active_root(), status="REVOKED")
    verdict = evaluate(book, revoked, "SH", "600000", as_of=T + 12 * MIN)
    assert verdict.status == NOT_ELIGIBLE
    assert verdict.reason == "AUTHORITY_REVOKED"
    assert not verdict.is_current_eligible and not verdict.is_fail_closed
    assert verdict.evidence_refs == ("authority:" + EVENT_ID,
                                     "signal:" + SIGNAL_ID)

    # A root whose window has ended is also NOT_ELIGIBLE (not fail-closed).
    expired = replace(active_root(), effective_until=T)
    verdict = evaluate(book, expired, "SH", "600000", as_of=T + 12 * MIN)
    assert verdict.status == NOT_ELIGIBLE
    assert verdict.reason == "AUTHORITY_WINDOW_ENDED"

    # A root missing its core fields fails closed (never guessed).
    verdict = evaluate(book, AuthorityRoot(), "SH", "600000",
                       as_of=T + 12 * MIN)
    assert verdict.status == FAIL_CLOSED
    assert verdict.reason == "AUTHORITY_ROOT_CORE_INVALID"
    assert not verdict.trading_permitted and not verdict.notification_permitted


# ----------------------------------------------------------------------
# Negative 2: HEALTH reports never enter the business path
# ----------------------------------------------------------------------
def test_health_report_never_enters_business_path() -> None:
    ctx = chain()
    store, pool, fanout = ctx["store"], ctx["pool"], ctx["fanout"]
    watcher_identity = watcher()

    # A real HEALTH signal enters the real pool (HEALTH is a pool kind).
    health_proof = envelope(event_id="game-health-event-1")
    store.freeze(health_proof)
    health_draft = SignalPoolDraft(
        signal_id="game-signal-health", report_kind="HEALTH",
        event_id="game-health-event-1",
        evidence_hash=health_proof.envelope_hash, evidence_version=1)
    health_entry = pool.append(health_draft, producer=watcher_identity,
                               store=store, now=T + 4 * MIN)
    assert health_entry.report_kind == "HEALTH"
    assert health_entry.status == STATUS_OPEN

    # The real upstream inbox accepts HEALTH as a report and issues a
    # receipt — upstream health telemetry is legitimate.
    health_report = QuantUpstreamReport(
        report_id="game-report-health", report_kind="HEALTH",
        event_id="game-health-event-1",
        evidence_hash=health_proof.envelope_hash, evidence_version=1,
        sender_agent_id=WATCHER_ID, receiver_agent_id=RECEIVER_ID,
        created_at=T + 5 * MIN)
    health_receipt = ctx["inbox"].accept(health_report, now=T + 5 * MIN)
    assert ctx["inbox"].is_active(health_receipt.receipt_id)

    # ...but the business fanout refuses every HEALTH plan attempt.
    with pytest.raises(InternalFanoutError, match="BUSINESS_SIGNAL_REQUIRED"):
        ctx["fanout"].plan(report=health_report,
                           receipt_id=health_receipt.receipt_id,
                           signal_id="game-signal-health", team=RESEARCH,
                           consumer_identity="researcher-1", now=T + 6 * MIN)
    with pytest.raises(InternalFanoutError, match="BUSINESS_SIGNAL_REQUIRED"):
        ctx["fanout"].plan(report=health_report,
                           receipt_id=health_receipt.receipt_id,
                           signal_id="game-signal-health", team=TRADING,
                           consumer_identity="trader-1", now=T + 6 * MIN)
    with pytest.raises(InternalFanoutError, match="SUBSCRIPTION_UNKNOWN"):
        ctx["fanout"].status(signal_id="game-signal-health", team=RESEARCH,
                             consumer_identity="researcher-1",
                             now=T + 9 * MIN)

    # The signal-reference bridge also refuses HEALTH references.
    health_intent = replace(ctx["intent"], draft=health_draft)
    with pytest.raises(SignalRefBridgeError, match="BUSINESS_SIGNAL_REQUIRED"):
        build_signal_ref(health_intent, health_entry)

    # The ANOMALY chain stays unaffected by the HEALTH detour.
    consume_both(fanout, report=ctx["report"],
                 receipt_id=ctx["receipt"].receipt_id, signal_id=SIGNAL_ID)
    assert ctx["fanout"].status(signal_id=SIGNAL_ID, team=RESEARCH,
                                consumer_identity="researcher-1",
                                now=T + 10 * MIN) == "LOGICAL_RECEIVED"


# ----------------------------------------------------------------------
# Negative 3: forged / impostor reports are refused by the fanout
# ----------------------------------------------------------------------
def test_forged_reports_are_refused_by_fanout_and_inbox() -> None:
    ctx = chain()
    inbox, fanout, receipt = ctx["inbox"], ctx["fanout"], ctx["receipt"]

    # (a) Same report_id with different content: hard conflict on re-accept.
    forged_variant = QuantUpstreamReport(
        report_id=REPORT_ID, report_kind="ANOMALY", event_id="forged-event-1",
        evidence_hash="c" * 64, evidence_version=1,
        sender_agent_id=WATCHER_ID, receiver_agent_id=RECEIVER_ID,
        created_at=T + 6 * MIN)
    with pytest.raises(UpstreamInboxError, match="RECEIPT_CONFLICT"):
        inbox.accept(forged_variant, now=T + 6 * MIN)

    # (b) A fabricated report that was never accepted: the fanout's trusted
    # receipt binding refuses it (receipt does not bind this report).
    fabricated = QuantUpstreamReport(
        report_id="fabricated-report-1", report_kind="ANOMALY",
        event_id=EVENT_ID, evidence_hash=ctx["proof"].envelope_hash,
        evidence_version=1, sender_agent_id=WATCHER_ID,
        receiver_agent_id=RECEIVER_ID, created_at=T + 6 * MIN)
    with pytest.raises(InternalFanoutError, match="UPSTREAM_NOT_ACTIVE"):
        fanout.plan(report=fabricated, receipt_id=receipt.receipt_id,
                    signal_id=SIGNAL_ID, team=RESEARCH,
                    consumer_identity="researcher-1", now=T + 6 * MIN)

    # (c) An impostor sender with well-formed content may be accepted by
    # the shape-only inbox ledger, but the fanout's independently read
    # SignalPoolEntry cross-bind refuses the producer mismatch.
    impostor_report = QuantUpstreamReport(
        report_id="impostor-report-1", report_kind="ANOMALY",
        event_id=EVENT_ID, evidence_hash=ctx["proof"].envelope_hash,
        evidence_version=1, sender_agent_id="impostor-agent",
        receiver_agent_id=RECEIVER_ID, created_at=T + 6 * MIN)
    impostor_receipt = inbox.accept(impostor_report, now=T + 6 * MIN)
    with pytest.raises(InternalFanoutError, match="SIGNAL_BINDING_INVALID"):
        fanout.plan(report=impostor_report,
                    receipt_id=impostor_receipt.receipt_id,
                    signal_id=SIGNAL_ID, team=RESEARCH,
                    consumer_identity="researcher-1", now=T + 6 * MIN)

    # The genuine report still plans cleanly afterwards.
    subscription = fanout.plan(report=ctx["report"],
                               receipt_id=receipt.receipt_id,
                               signal_id=SIGNAL_ID, team=RESEARCH,
                               consumer_identity="researcher-1",
                               now=T + 7 * MIN)
    assert subscription.signal_id == SIGNAL_ID
    assert subscription.consumer_identity == "researcher-1"


# ----------------------------------------------------------------------
# Negative 4: a future plan is invisible to earlier as-of reads
# ----------------------------------------------------------------------
def test_future_plan_invisible_to_earlier_asof_reads() -> None:
    book = ResearchPlanBook()

    # Only plan in the book is published in the future.
    future_plan = ResearchPlan(
        ref=PlanRef("game-plan-future", 1), market="SH", symbol="600000",
        side="BUY", thesis="synthetic future hypothesis",
        entry_condition="future entry evidence",
        exit_condition="future exit evidence", profile_id=PROFILE,
        profile_version=PROFILE_VERSION,
        risk_budget_description="abstract offline risk template",
        published_at=T + 20 * MIN, effective_from=T + 21 * MIN,
        effective_until=at(60), approver_id="game-approver")
    book.publish(future_plan)

    early = evaluate(book, active_root(), "SH", "600000",
                     as_of=T + 10 * MIN)
    assert early.status == NOT_ELIGIBLE
    assert early.reason == "NO_CURRENT_PLAN"
    assert not early.is_fail_closed

    late = evaluate(book, active_root(), "SH", "600000",
                    as_of=T + 22 * MIN)
    assert late.status == CURRENT_ELIGIBLE
    assert "plan:game-plan-future:1" in late.evidence_refs

    # With a real lineage, earlier reads see only the earlier version —
    # no lookahead into the not-yet-published successor.
    lineage = ResearchPlanBook()
    lineage.publish(ResearchPlan(
        ref=PlanRef(PLAN_ID, 1), market="SH", symbol="600000", side="BUY",
        thesis="synthetic v1 hypothesis",
        entry_condition="v1 entry evidence", exit_condition="v1 exit evidence",
        profile_id=PROFILE, profile_version=PROFILE_VERSION,
        risk_budget_description="abstract offline risk template",
        published_at=T, effective_from=T + MIN, effective_until=at(40),
        approver_id="game-approver"))
    lineage.publish(ResearchPlan(
        ref=PlanRef(PLAN_ID, 2), market="SH", symbol="600000", side="BUY",
        thesis="synthetic v2 hypothesis",
        entry_condition="v2 entry evidence", exit_condition="v2 exit evidence",
        profile_id=PROFILE, profile_version=PROFILE_VERSION,
        risk_budget_description="abstract offline risk template",
        published_at=T + 20 * MIN, effective_from=T + 21 * MIN,
        effective_until=at(60), approver_id="game-approver"))
    before = evaluate(lineage, active_root(), "SH", "600000",
                      as_of=T + 10 * MIN)
    after = evaluate(lineage, active_root(), "SH", "600000",
                     as_of=T + 22 * MIN)
    assert before.status == CURRENT_ELIGIBLE
    assert "plan:" + PLAN_ID + ":1" in before.evidence_refs
    assert "plan:" + PLAN_ID + ":2" not in before.evidence_refs
    assert after.status == CURRENT_ELIGIBLE
    assert "plan:" + PLAN_ID + ":2" in after.evidence_refs
    assert "plan:" + PLAN_ID + ":1" not in after.evidence_refs


# ----------------------------------------------------------------------
# Bridge digest semantics: audit reference only, never a permission
# ----------------------------------------------------------------------
def test_bridge_digests_are_audit_references_not_permissions() -> None:
    ctx = chain()
    intent, entry = ctx["intent"], ctx["entry"]
    receipt, report = ctx["receipt"], ctx["report"]

    signal_ref = build_signal_ref(intent, entry)
    acceptance_ref = build_acceptance_ref(
        report, receipt, inbox_epoch=ctx["inbox"].ledger_epoch,
        inbox_receipt_lookup=ctx["inbox_lookup"])

    for ref in (signal_ref, acceptance_ref):
        assert ref.is_permission is False
        assert ref.production_permitted is False
        assert ref.notification_permitted is False
        assert ref.trading_permitted is False
        with pytest.raises(SignalRefBridgeError, match="NOT_A_PERMISSION"):
            ref.permission_token()

    # A revoked receipt can never rebuild the acceptance reference: the
    # trusted inbox lookup returns nothing after revocation. The caller's
    # observed epoch is the one frozen at build time (receipt.ledger_epoch);
    # the live inbox epoch advanced on revoke, which is exactly why the
    # trusted lookup — not the epoch counter — is the liveness gate.
    ctx["inbox"].revoke(receipt.receipt_id, now=T + 20 * MIN)
    with pytest.raises(SignalRefBridgeError, match="RECEIPT_NOT_ACTIVE"):
        build_acceptance_ref(report, receipt,
                             inbox_epoch=receipt.ledger_epoch,
                             inbox_receipt_lookup=ctx["inbox_lookup"])
    with pytest.raises(SignalRefBridgeError, match="RECEIPT_NOT_ACTIVE"):
        verify_acceptance_ref(
            acceptance_ref, report, receipt,
            inbox_epoch=receipt.ledger_epoch,
            inbox_receipt_lookup=ctx["inbox_lookup"])
    # The signal reference is still verifiable: it binds pool + entry
    # provenance, not upstream receipt liveness.
    assert verify_signal_ref(signal_ref, intent, entry) is signal_ref


# ----------------------------------------------------------------------
# Determinism: identical arguments always yield identical outputs
# ----------------------------------------------------------------------
def test_identical_inputs_are_deterministic() -> None:
    ctx = chain()
    intent, entry = ctx["intent"], ctx["entry"]
    receipt, report = ctx["receipt"], ctx["report"]
    book = ctx["book"]

    # Idempotent inbox re-acceptance returns the exact same receipt/ACK.
    again = ctx["inbox"].accept(report, now=T + 6 * MIN)
    assert again == receipt
    assert again.receipt_id == receipt.receipt_id
    assert again.ack.ack_id == receipt.ack.ack_id
    assert report.envelope_hash == report.envelope_hash  # stable content hash

    # Bridge builders are pure: identical inputs rebuild identical refs.
    first_signal_ref = build_signal_ref(intent, entry)
    second_signal_ref = build_signal_ref(intent, entry)
    first_acceptance_ref = build_acceptance_ref(
        report, receipt, inbox_epoch=ctx["inbox"].ledger_epoch,
        inbox_receipt_lookup=ctx["inbox_lookup"])
    second_acceptance_ref = build_acceptance_ref(
        report, receipt, inbox_epoch=ctx["inbox"].ledger_epoch,
        inbox_receipt_lookup=ctx["inbox_lookup"])
    assert first_signal_ref == second_signal_ref
    assert first_acceptance_ref == second_acceptance_ref

    # Idempotent fanout plan returns the existing subscription.
    subscription = ctx["fanout"].plan(
        report=report, receipt_id=receipt.receipt_id, signal_id=SIGNAL_ID,
        team=RESEARCH, consumer_identity="researcher-1", now=T + 6 * MIN)
    repeat = ctx["fanout"].plan(
        report=report, receipt_id=receipt.receipt_id, signal_id=SIGNAL_ID,
        team=RESEARCH, consumer_identity="researcher-1", now=T + 6 * MIN)
    assert subscription == repeat

    # Eligibility evaluation is a deterministic function of its arguments.
    book.match_signal(ResearchSignal(
        signal_id=SIGNAL_ID, market="SH", symbol="600000", side="BUY",
        profile_id=PROFILE, profile_version=PROFILE_VERSION,
        triggered_at=T + 3 * MIN, observed_at=T + 10 * MIN))
    first_verdict = evaluate(book, active_root(), "SH", "600000",
                             as_of=T + 12 * MIN)
    second_verdict = evaluate(book, active_root(), "SH", "600000",
                              as_of=T + 12 * MIN)
    assert first_verdict == second_verdict
    assert first_verdict.status == CURRENT_ELIGIBLE


# ----------------------------------------------------------------------
# Zero production side effects: pure reads never mutate real ledgers
# ----------------------------------------------------------------------
def test_pure_reads_leave_real_ledgers_unchanged() -> None:
    ctx = chain()
    pool, inbox, book, store = ctx["pool"], ctx["inbox"], ctx["book"], ctx["store"]

    # Consume the business path so the ledgers reach their steady state.
    consume_both(ctx["fanout"], report=ctx["report"],
                 receipt_id=ctx["receipt"].receipt_id, signal_id=SIGNAL_ID)

    pool_before = dict(pool._entries)
    inbox_before = dict(inbox._receipts)
    book_plans_before = dict(book._plans)
    book_signals_before = dict(book._signals)
    store_before = {identity: dict(versions)
                    for identity, versions in store._versions.items()}

    # Pure reads: eligibility evaluation and bridge build/verify only.
    verdict = evaluate(book, active_root(), "SH", "600000",
                       as_of=T + 12 * MIN)
    assert verdict.status == CURRENT_ELIGIBLE
    signal_ref = build_signal_ref(ctx["intent"], ctx["entry"])
    assert verify_signal_ref(signal_ref, ctx["intent"], ctx["entry"])
    acceptance_ref = build_acceptance_ref(
        ctx["report"], ctx["receipt"], inbox_epoch=ctx["inbox"].ledger_epoch,
        inbox_receipt_lookup=ctx["inbox_lookup"])
    assert verify_acceptance_ref(
        acceptance_ref, ctx["report"], ctx["receipt"],
        inbox_epoch=ctx["inbox"].ledger_epoch,
        inbox_receipt_lookup=ctx["inbox_lookup"])

    assert pool._entries == pool_before
    assert inbox._receipts == inbox_before
    assert book._plans == book_plans_before
    assert book._signals == book_signals_before
    assert store._versions == store_before

    # Every chain object stays non-executable: no production, notification
    # or trading permission exists anywhere on the offline slice. (The
    # receipt is a plain frozen binding and carries no permission surface.)
    for obj in (ctx["intent"], ctx["entry"], ctx["proof"]):
        assert not obj.production_permitted
        assert not obj.notification_permitted
        assert not obj.trading_permitted
    assert not ctx["pool"].production_permitted
    assert not ctx["pool"].trading_permitted


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
