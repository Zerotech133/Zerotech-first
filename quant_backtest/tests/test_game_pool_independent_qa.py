"""Independent offline adversarial checks for the game stock-pool adapter."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from quant_backtest.watch_dispatch.agent_reporting import AgentIdentity
from quant_backtest.watch_dispatch.contracts import freeze_offline
from quant_backtest.watch_dispatch.evidence_store import EvidenceStore
from quant_backtest.watch_dispatch.signal_pool import SignalPool
from quant_backtest.watch_dispatch.stock_pool_source import (
    BLOCK_CUTOVER,
    OFFLINE_SHADOW,
    RESEARCH_ONLY,
    PoolMember,
    StockPoolError,
    StockPoolSnapshot,
    StockPoolSource,
)

BASE = datetime(2026, 10, 20, 2, tzinfo=timezone.utc)
MINUTE = timedelta(minutes=1)
MEMBER = PoolMember("SH", "600000", "membership-1")


def at(minutes: int) -> datetime:
    return BASE + minutes * MINUTE


def make_snapshot(*, status: str = OFFLINE_SHADOW, version: int = 1,
                  members: tuple[PoolMember, ...] = (MEMBER,),
                  published: int = 0, visible: int = 1,
                  expires: int = 20) -> StockPoolSnapshot:
    return StockPoolSnapshot(
        "pool-1", version, "publisher-1", members, at(published),
        at(visible), at(expires), "a" * 64, status,
    )


def make_evidence(*, event_id: str = "event-1", source: int = 2,
                  visible: int = 2, frozen: int = 2, expires: int = 15,
                  member: str = "membership-1", market: str = "SH",
                  symbol: str = "600000"):
    return freeze_offline(
        schema_version=1, evidence_version=1, previous_version_hash=None,
        event_id=event_id, pool="B", tenant_or_owner_id="owner-1",
        market=market, code=symbol, source_membership_id=member,
        source_revision="revision-1", rule_id="rule-1", rule_version=1,
        comparator="GE", threshold="1.00", unit="ratio",
        window="5m", observed_value="1.10", baseline_value="1.00",
        raw_ref="raw:1", raw_digest="b" * 64, source_id="synthetic",
        calendar_id="synthetic", source_as_of=at(source),
        visible_at=at(visible), detected_at=at(frozen),
        freeze_at=at(frozen), valid_until=at(expires),
        facts=("synthetic",), hypotheses=(), supporting_refs=("raw:1",),
        contradicting_refs=(), confirmation_condition="synthetic confirmation",
        invalidation_condition="synthetic invalidation",
    )


@pytest.fixture
def context():
    source = StockPoolSource(frozenset({"publisher-1"}))
    source.publish(make_snapshot(), now=at(0))
    watcher = AgentIdentity("xuchu-1", "WATCH", "registry-1", at(0), at(20))
    choice = source.select("pool-1", market="SH", symbol="600000",
                           watcher=watcher, as_of=at(1))
    evidence = make_evidence()
    store = EvidenceStore()
    store.freeze(evidence)
    signal_pool = SignalPool(store)
    return source, choice, evidence, store, signal_pool


def prepare(context, *, evidence=None, store=None, signal_id="signal-1",
            trigger_at=at(3), now=at(3), triggered=True):
    source, selection, frozen, evidence_store, _ = context
    return source.prepare_trigger(
        selection, triggered=triggered,
        evidence=frozen if evidence is None else evidence,
        store=evidence_store if store is None else store,
        signal_id=signal_id, trigger_at=trigger_at, now=now,
    )


class TestIsolationAndMembership:
    def test_research_pool_cannot_be_promoted_or_prepare_draft(self):
        source, choice, evidence, store, signal_pool = context_data(RESEARCH_ONLY)
        with pytest.raises(StockPoolError, match="RESEARCH_CUTOVER_BLOCKED"):
            source.publish(make_snapshot(status=OFFLINE_SHADOW, version=2,
                                         published=2, visible=2), now=at(2))
        with pytest.raises(StockPoolError, match="RESEARCH_CUTOVER_BLOCKED"):
            source.prepare_trigger(choice, triggered=True, evidence=evidence,
                                   store=store, signal_id="signal-1",
                                   trigger_at=at(3), now=at(3))
        assert choice.snapshot.cutover_status == BLOCK_CUTOVER
        assert not choice.snapshot.production_permitted
        assert not choice.snapshot.notification_permitted
        assert not choice.snapshot.trading_permitted
        assert signal_pool.list_open(at(3)) == ()

    def test_explicit_trigger_only_creates_draft_not_signal_entry(self, context):
        _, selection, evidence, _, pool = context
        with pytest.raises(StockPoolError, match="TRIGGER_NOT_CONFIRMED"):
            prepare(context, triggered=False)
        intent = prepare(context)
        assert intent.draft.signal_id == "signal-1"
        assert intent.draft.event_id == evidence.event_id
        assert intent.draft.evidence_hash == evidence.envelope_hash
        assert intent.draft.evidence_version == evidence.evidence_version
        assert intent.producer_agent_id == selection.watcher.agent_id
        assert (intent.market, intent.symbol, intent.membership_id) == (
            evidence.market, evidence.code, evidence.source_membership_id)
        assert intent.cutover_status == BLOCK_CUTOVER
        assert not intent.production_permitted
        assert not intent.notification_permitted
        assert not intent.trading_permitted
        assert pool.list_open(at(3)) == ()

    @pytest.mark.parametrize("difference", ["market", "symbol", "member"])
    def test_exact_membership_identity_required(self, context, difference):
        changes = {"market": {"market": "SZ"},
                   "symbol": {"symbol": "600001"},
                   "member": {"member": "membership-2"}}[difference]
        evidence = make_evidence(**changes)
        store = EvidenceStore()
        store.freeze(evidence)
        with pytest.raises(StockPoolError, match="EVIDENCE_MEMBER_MISMATCH"):
            prepare(context, evidence=evidence, store=store)
        assert context[-1].list_open(at(3)) == ()


def context_data(status: str):
    source = StockPoolSource(frozenset({"publisher-1"}))
    source.publish(make_snapshot(status=status), now=at(0))
    watcher = AgentIdentity("xuchu-1", "WATCH", "registry-1", at(0), at(20))
    selection = source.select("pool-1", market="SH", symbol="600000",
                              watcher=watcher, as_of=at(1))
    evidence = make_evidence()
    store = EvidenceStore()
    store.freeze(evidence)
    return source, selection, evidence, store, SignalPool(store)


class TestTemporalAndDedupe:
    def test_preselection_market_bar_frozen_after_selection_is_usable(self, context):
        """Market data can precede a choice without being visible in the future."""
        evidence = make_evidence(source=0, visible=2, frozen=2)
        store = EvidenceStore()
        store.freeze(evidence)
        intent = prepare(context, evidence=evidence, store=store)
        assert intent.draft.event_id == evidence.event_id
        assert context[-1].list_open(at(3)) == ()

    def test_evidence_frozen_after_trigger_is_rejected(self, context):
        evidence = make_evidence(source=2, visible=2, frozen=4)
        store = EvidenceStore()
        store.freeze(evidence)
        with pytest.raises(StockPoolError, match="EVIDENCE_(INVALID|NOT_VISIBLE_AT_TRIGGER)"):
            prepare(context, evidence=evidence, store=store)

    def test_revocation_and_expiry_fail_closed_at_boundaries(self, context):
        source = context[0]
        source.revoke("pool-1", 1, now=at(3))
        with pytest.raises(StockPoolError, match="SNAPSHOT_NOT_VISIBLE"):
            prepare(context)
        assert source.get("pool-1", 1, as_of=at(2)) is context[1].snapshot
        with pytest.raises(StockPoolError, match="SNAPSHOT_NOT_VISIBLE"):
            source.get("pool-1", 1, as_of=at(3))
        assert context[-1].list_open(at(3)) == ()

    def test_rejected_attempt_does_not_consume_signal_id(self, context):
        with pytest.raises(StockPoolError, match="TRIGGER_NOT_CONFIRMED"):
            prepare(context, triggered=False)
        assert prepare(context).draft.signal_id == "signal-1"

    def test_duplicate_event_across_evidence_revision_rejected(self, context):
        first = prepare(context)
        evidence = make_evidence(event_id=first.draft.event_id,
                                 source=3, visible=3, frozen=3)
        store = EvidenceStore()
        store.freeze(evidence)
        with pytest.raises(StockPoolError, match="TRIGGER_DUPLICATE"):
            prepare(context, evidence=evidence, store=store,
                    signal_id="signal-2", trigger_at=at(4), now=at(4))
        assert context[-1].list_open(at(4)) == ()

    def test_same_signal_id_for_distinct_event_rejected(self, context):
        prepare(context)
        evidence = make_evidence(event_id="event-2", source=3,
                                 visible=3, frozen=3)
        store = EvidenceStore()
        store.freeze(evidence)
        with pytest.raises(StockPoolError, match="TRIGGER_DUPLICATE"):
            prepare(context, evidence=evidence, store=store,
                    trigger_at=at(4), now=at(4))

    def test_frozen_lookalike_is_not_authorized(self, context):
        _, _, evidence, _, pool = context
        with pytest.raises(StockPoolError, match="EVIDENCE_NOT_FROZEN"):
            prepare(context, evidence=replace(evidence))
        assert pool.list_open(at(3)) == ()

    def test_future_visible_information_cannot_prepare_past_trigger(self, context):
        evidence = make_evidence(source=0, visible=4, frozen=4)
        store = EvidenceStore()
        store.freeze(evidence)
        with pytest.raises(StockPoolError, match="EVIDENCE_(INVALID|NOT_VISIBLE_AT_TRIGGER)"):
            prepare(context, evidence=evidence, store=store,
                    trigger_at=at(3), now=at(5))
        assert context[-1].list_open(at(5)) == ()

    def test_publisher_commit_cannot_be_backdated_or_backfilled(self):
        source = StockPoolSource(frozenset({"publisher-1"}))
        source.publish(make_snapshot(), now=at(0))
        with pytest.raises(StockPoolError, match="PUBLISHED_AT_COMMIT_MISMATCH"):
            source.publish(make_snapshot(version=2, published=2, visible=2),
                           now=at(4))
        valid = make_snapshot(version=2, published=5, visible=5)
        assert source.publish(valid, now=at(5)) is valid
        assert source.current("pool-1", as_of=at(4)).version == 1
        assert source.current("pool-1", as_of=at(5)) is valid

    def test_revocation_cannot_rewrite_prior_commit_history(self):
        source = StockPoolSource(frozenset({"publisher-1"}))
        source.publish(make_snapshot(), now=at(0))
        source.publish(make_snapshot(version=2, published=4, visible=4),
                       now=at(4))
        with pytest.raises(StockPoolError, match="COMMIT_TIME_NOT_MONOTONIC"):
            source.revoke("pool-1", 1, now=at(3))
        assert source.get("pool-1", 1, as_of=at(3)).version == 1
        source.revoke("pool-1", 1, now=at(5))
        assert source.get("pool-1", 1, as_of=at(4)).version == 1
        with pytest.raises(StockPoolError, match="SNAPSHOT_NOT_VISIBLE"):
            source.get("pool-1", 1, as_of=at(5))

    def test_global_commit_fence_does_not_allow_cross_pool_backfill(self):
        source = StockPoolSource(frozenset({"publisher-1"}))
        source.publish(make_snapshot(), now=at(0))
        source.revoke("pool-1", 1, now=at(5))
        other = replace(make_snapshot(published=4, visible=4), pool_id="pool-2")
        with pytest.raises(StockPoolError, match="COMMIT_TIME_NOT_MONOTONIC"):
            source.publish(other, now=at(4))
        with pytest.raises(StockPoolError, match="SNAPSHOT_NOT_VISIBLE"):
            source.current("pool-2", as_of=at(6))
