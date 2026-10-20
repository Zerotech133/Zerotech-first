"""G0/G1 synthetic stock-pool and explicit-trigger adapter regression."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from quant_backtest.watch_dispatch.agent_reporting import AgentIdentity
from quant_backtest.watch_dispatch.contracts import freeze_offline
from quant_backtest.watch_dispatch.evidence_store import EvidenceStore
from quant_backtest.watch_dispatch.signal_pool import SignalPool
from quant_backtest.watch_dispatch.stock_pool_source import (
    OFFLINE_SHADOW, RESEARCH_ONLY, BLOCK_CUTOVER, PoolMember, StockPoolError,
    StockPoolSnapshot, StockPoolSource,
)

T = datetime(2026, 10, 20, 2, 0, tzinfo=timezone.utc)
MIN = timedelta(minutes=1)
MEMBER = PoolMember("SH", "600000", "member-1")


def snapshot(*, version: int = 1, pool_id: str = "synthetic", publisher: str = "test-pub",
             members: tuple[PoolMember, ...] = (MEMBER,), published_at: datetime = T,
             visible_at: datetime = T + MIN, valid_until: datetime = T + 20 * MIN,
             status: str = OFFLINE_SHADOW,
             source_manifest: str = "a" * 64) -> StockPoolSnapshot:
    return StockPoolSnapshot(pool_id, version, publisher, members, published_at,
                             visible_at, valid_until, source_manifest, status)


def source() -> StockPoolSource:
    pool_source = StockPoolSource(frozenset({"test-pub"}))
    pool_source.publish(snapshot(), now=T)
    return pool_source


def watcher(*, role: str = "WATCH", valid_until: datetime = T + 30 * MIN) -> AgentIdentity:
    return AgentIdentity("watch-1", role, "test-register", T - MIN, valid_until)


def envelope(*, market: str = "SH", code: str = "600000",
             member: str = "member-1", evidence_time: datetime = T + 2 * MIN,
             until: datetime = T + 15 * MIN):
    return freeze_offline(
        schema_version=1, evidence_version=1, previous_version_hash=None,
        pool="B", tenant_or_owner_id="owner-1", market=market, code=code,
        source_membership_id=member, source_revision="revision-1", rule_id="rule-test",
        rule_version=1, comparator="GE", threshold="1.00", unit="ratio",
        window="5m", observed_value="1.10", baseline_value="1.00",
        raw_ref="raw:test", raw_digest="b" * 64, source_id="test-source",
        calendar_id="test-calendar", source_as_of=evidence_time,
        visible_at=evidence_time, detected_at=evidence_time, freeze_at=evidence_time,
        valid_until=until, facts=("synthetic",), hypotheses=("hypothesis",),
        supporting_refs=("raw:test",), contradicting_refs=(),
        confirmation_condition="synthetic condition", invalidation_condition="synthetic invalidate",
        event_id="event-1",
    )


def prepared_fixture():
    stock_source = source()
    selection = stock_source.select("synthetic", market="SH", symbol="600000",
                                    watcher=watcher(), as_of=T + MIN)
    proof = envelope()
    evidence_store = EvidenceStore()
    evidence_store.freeze(proof)
    signal_pool = SignalPool(evidence_store)
    return stock_source, selection, proof, evidence_store, signal_pool


def prepare(stock_source, selection, proof, evidence_store, *, triggered=True,
            trigger_at=T + 3 * MIN, now=T + 3 * MIN, signal_id="signal-1"):
    return stock_source.prepare_trigger(selection, triggered=triggered, evidence=proof,
                                        store=evidence_store, signal_id=signal_id,
                                        trigger_at=trigger_at, now=now)


def test_snapshot_immutability_freeze_and_membership_identity():
    pool = source()
    selected = pool.select("synthetic", market="SH", symbol="600000",
                           watcher=watcher(), as_of=T + MIN)
    assert selected.snapshot is pool.get("synthetic", 1, as_of=T + MIN)
    assert selected.member == MEMBER
    assert selected.snapshot.cutover_status == BLOCK_CUTOVER
    assert not selected.snapshot.production_permitted
    with pytest.raises(AttributeError):
        selected.snapshot.version = 2
    with pytest.raises(StockPoolError, match="MEMBERS_DUPLICATE"):
        snapshot(members=(MEMBER, MEMBER))
    with pytest.raises(StockPoolError, match="MEMBERSHIP_DUPLICATE"):
        snapshot(members=(MEMBER, PoolMember("SZ", "000001", MEMBER.membership_id)))
    with pytest.raises(StockPoolError, match="MEMBERS_INVALID"):
        snapshot(members=())


@pytest.mark.parametrize("changes,code", [
    ({"visible_at": T - MIN}, "TIME_ORDER_INVALID"),
    ({"valid_until": T + MIN}, "TIME_ORDER_INVALID"),
    ({"source_manifest": "not-a-digest"}, "SOURCE_MANIFEST_INVALID"),
    ({"version": True}, "VERSION_INVALID"),
    ({"status": "PRODUCTION"}, "STATUS_INVALID"),
    ({"visible_at": datetime(2026, 10, 20, 2, 1)}, "VISIBLE_AT_INVALID"),
    ({"published_at": T.astimezone(timezone(timedelta(hours=8)))}, "PUBLISHED_AT_INVALID"),
])
def test_snapshot_rejects_invalid_contract(changes, code):
    with pytest.raises(StockPoolError, match=code):
        snapshot(**changes)


def test_publisher_version_and_future_publish_fail_closed():
    stock_source = source()
    with pytest.raises(StockPoolError, match="PUBLISHER_UNAUTHORIZED"):
        stock_source.publish(snapshot(pool_id="other", publisher="untrusted"), now=T)
    with pytest.raises(StockPoolError, match="PUBLISHED_AT_COMMIT_MISMATCH"):
        stock_source.publish(snapshot(pool_id="other", published_at=T + MIN), now=T)
    for invalid in (snapshot(), snapshot(version=3, published_at=T + 3 * MIN,
                                          visible_at=T + 3 * MIN)):
        with pytest.raises(StockPoolError, match="PUBLISHED_AT_COMMIT_MISMATCH"):
            stock_source.publish(invalid, now=T + 4 * MIN)
    with pytest.raises(StockPoolError, match="COMMIT_TIME_NOT_MONOTONIC"):
        stock_source.publish(snapshot(), now=T)
    second = snapshot(version=2, published_at=T + 2 * MIN,
                      visible_at=T + 4 * MIN)
    stock_source.publish(second, now=T + 2 * MIN)
    assert stock_source.current("synthetic", as_of=T + 3 * MIN).version == 1
    assert stock_source.current("synthetic", as_of=T + 4 * MIN) is second
    assert stock_source.get("synthetic", 1, as_of=T + 4 * MIN).version == 1


def test_visibility_expiry_and_revocation_historical_asof():
    stock_source = source()
    for instant in (T - MIN, T, T + 20 * MIN):
        with pytest.raises(StockPoolError, match="SNAPSHOT_NOT_VISIBLE"):
            stock_source.get("synthetic", 1, as_of=instant)
    with pytest.raises(StockPoolError, match="MEMBER_NOT_FOUND"):
        stock_source.select("synthetic", market="SZ", symbol="600000",
                            watcher=watcher(), as_of=T + MIN)
    with pytest.raises(StockPoolError, match="WATCHER_ROLE_INVALID"):
        stock_source.select("synthetic", market="SH", symbol="600000",
                            watcher=watcher(role="QUANT_SECTOR"), as_of=T + MIN)
    with pytest.raises(StockPoolError, match="WATCHER_NOT_CURRENT"):
        stock_source.select("synthetic", market="SH", symbol="600000",
                            watcher=watcher(valid_until=T + MIN), as_of=T + MIN)
    stock_source.revoke("synthetic", 1, now=T + 5 * MIN)
    assert stock_source.get("synthetic", 1, as_of=T + 5 * MIN - timedelta(seconds=1))
    with pytest.raises(StockPoolError, match="SNAPSHOT_NOT_VISIBLE"):
        stock_source.current("synthetic", as_of=T + 5 * MIN)
    with pytest.raises(StockPoolError, match="ALREADY_REVOKED"):
        stock_source.revoke("synthetic", 1, now=T + 6 * MIN)


def test_no_fallback_to_revoked_latest_version():
    stock_source = source()
    stock_source.publish(snapshot(version=2, published_at=T + 2 * MIN,
                                  visible_at=T + 2 * MIN), now=T + 2 * MIN)
    stock_source.revoke("synthetic", 2, now=T + 3 * MIN)
    assert stock_source.get("synthetic", 1, as_of=T + 3 * MIN)
    with pytest.raises(StockPoolError, match="SNAPSHOT_NOT_VISIBLE"):
        stock_source.current("synthetic", as_of=T + 3 * MIN)


def test_research_only_never_promoted_even_by_new_version():
    stock_source = StockPoolSource(frozenset({"test-pub"}))
    research = snapshot(status=RESEARCH_ONLY)
    stock_source.publish(research, now=T)
    with pytest.raises(StockPoolError, match="RESEARCH_CUTOVER_BLOCKED"):
        stock_source.publish(snapshot(version=2, published_at=T + 2 * MIN,
                                      visible_at=T + 2 * MIN), now=T + 2 * MIN)
    selection = stock_source.select("synthetic", market="SH", symbol="600000",
                                    watcher=watcher(), as_of=T + MIN)
    proof = envelope()
    store = EvidenceStore()
    store.freeze(proof)
    with pytest.raises(StockPoolError, match="RESEARCH_CUTOVER_BLOCKED"):
        prepare(stock_source, selection, proof, store)


def test_untriggered_never_enters_signal_pool_and_explicit_draft_needs_append():
    stock_source, selection, proof, store, signal_pool = prepared_fixture()
    with pytest.raises(StockPoolError, match="TRIGGER_NOT_CONFIRMED"):
        prepare(stock_source, selection, proof, store, triggered=False)
    assert signal_pool.list_open(T + 3 * MIN) == ()
    intent = prepare(stock_source, selection, proof, store)
    assert intent.pool_id == "synthetic" and intent.pool_version == 1
    assert intent.membership_id == MEMBER.membership_id
    assert intent.rule_id == proof.rule_id and intent.rule_version == proof.rule_version
    assert intent.draft.event_id == proof.event_id
    assert intent.draft.evidence_hash == proof.envelope_hash
    assert intent.producer_agent_id == selection.watcher.agent_id
    assert intent.status == "OFFLINE_DRAFT_ONLY"
    assert not intent.production_permitted and not intent.notification_permitted
    assert not intent.trading_permitted and intent.cutover_status == BLOCK_CUTOVER
    assert signal_pool.list_open(T + 3 * MIN) == ()
    with pytest.raises(AttributeError):
        intent.pool_version = 2


@pytest.mark.parametrize("mode,code", [
    ("unfrozen", "EVIDENCE_NOT_FROZEN"),
    ("lookalike", "EVIDENCE_NOT_FROZEN"),
    ("missing", "EVIDENCE_NOT_FROZEN"),
    ("different_market", "EVIDENCE_MEMBER_MISMATCH"),
    ("different_symbol", "EVIDENCE_MEMBER_MISMATCH"),
    ("different_member", "EVIDENCE_MEMBER_MISMATCH"),
    ("late_evidence", "EVIDENCE_INVALID"),
    ("expired_evidence", "EVIDENCE_INVALID"),
    ("future_trigger", "SELECTION_NOT_CURRENT"),
    ("backdated_trigger", "SELECTION_NOT_CURRENT"),
])
def test_trigger_fail_closed_on_evidence_and_time(mode, code):
    stock_source, selection, proof, store, signal_pool = prepared_fixture()
    trigger_at, now = T + 3 * MIN, T + 3 * MIN
    if mode == "unfrozen":
        store = EvidenceStore()
    elif mode == "lookalike":
        proof = replace(proof)
    elif mode == "missing":
        proof = None
    elif mode == "different_market":
        proof = envelope(market="SZ")
        store.freeze(proof)
    elif mode == "different_symbol":
        proof = envelope(code="000001")
        store.freeze(proof)
    elif mode == "different_member":
        proof = envelope(member="member-2")
        store = EvidenceStore()
        store.freeze(proof)
    elif mode == "late_evidence":
        proof = envelope(evidence_time=T + 4 * MIN)
        store = EvidenceStore()
        store.freeze(proof)
    elif mode == "expired_evidence":
        proof = envelope(until=T + 3 * MIN)
        store = EvidenceStore()
        store.freeze(proof)
    elif mode == "future_trigger":
        trigger_at = T + 4 * MIN
    else:
        trigger_at = T
    with pytest.raises(StockPoolError, match=code):
        prepare(stock_source, selection, proof, store,
                trigger_at=trigger_at, now=now)
    assert signal_pool.list_open(now) == ()


def test_preselection_sampling_visible_after_choice_is_allowed_but_not_auto_appended():
    stock_source, selection, _, _, signal_pool = prepared_fixture()
    # The observation occurred before XuChu chose the ticket, but the source
    # did not become visible/frozen until after selection and before trigger.
    proof = freeze_offline(
        schema_version=1, evidence_version=1, previous_version_hash=None,
        pool="B", tenant_or_owner_id="owner-1", market="SH", code="600000",
        source_membership_id="member-1", source_revision="revision-1", rule_id="rule-test",
        rule_version=1, comparator="GE", threshold="1.00", unit="ratio",
        window="5m", observed_value="1.10", baseline_value="1.00",
        raw_ref="raw:test", raw_digest="b" * 64, source_id="test-source",
        calendar_id="test-calendar", source_as_of=T - MIN,
        visible_at=T + 2 * MIN, detected_at=T + 2 * MIN, freeze_at=T + 2 * MIN,
        valid_until=T + 15 * MIN, facts=("synthetic",), hypotheses=("hypothesis",),
        supporting_refs=("raw:test",), contradicting_refs=(),
        confirmation_condition="synthetic condition", invalidation_condition="synthetic invalidate",
        event_id="preselection-event",
    )
    store = EvidenceStore()
    store.freeze(proof)
    intent = prepare(stock_source, selection, proof, store)
    assert proof.source_as_of < selection.selected_at < proof.visible_at <= intent.trigger_at
    assert intent.draft.event_id == proof.event_id
    assert signal_pool.list_open(T + 3 * MIN) == ()


def test_trigger_evidence_visible_at_trigger_not_only_source_as_of():
    stock_source, selection, _, _, signal_pool = prepared_fixture()
    proof = freeze_offline(
        schema_version=1, evidence_version=1, previous_version_hash=None,
        pool="B", tenant_or_owner_id="owner-1", market="SH", code="600000",
        source_membership_id="member-1", source_revision="revision-1", rule_id="rule-test",
        rule_version=1, comparator="GE", threshold="1.00", unit="ratio",
        window="5m", observed_value="1.10", baseline_value="1.00",
        raw_ref="raw:test", raw_digest="b" * 64, source_id="test-source",
        calendar_id="test-calendar", source_as_of=T - MIN,
        visible_at=T + 4 * MIN, detected_at=T + 4 * MIN, freeze_at=T + 4 * MIN,
        valid_until=T + 15 * MIN, facts=("synthetic",), hypotheses=("hypothesis",),
        supporting_refs=("raw:test",), contradicting_refs=(),
        confirmation_condition="synthetic condition", invalidation_condition="synthetic invalidate",
        event_id="future-visible-event",
    )
    store = EvidenceStore()
    store.freeze(proof)
    with pytest.raises(StockPoolError, match="EVIDENCE_INVALID"):
        prepare(stock_source, selection, proof, store)
    assert signal_pool.list_open(T + 3 * MIN) == ()


def test_g2_signal_id_event_id_are_distinct_and_not_authoritative_mapping():
    stock_source, selection, proof, store, signal_pool = prepared_fixture()
    intent = prepare(stock_source, selection, proof, store, signal_id="different-signal-id")
    assert intent.draft.signal_id == "different-signal-id"
    assert intent.draft.event_id == proof.event_id
    assert intent.draft.signal_id != intent.draft.event_id
    assert intent.pool_version == 1 and intent.source_manifest == selection.snapshot.source_manifest
    assert signal_pool.get(intent.draft.signal_id) is None
    # signal_id and event_id are distinct identities: AgentReport and the
    # upstream mapping carry the evidence envelope's event_id, not signal_id.
    # G2 independently checks the SignalPoolEntry; this draft is not that entry.


def test_prepare_rechecks_current_version_revoke_expiry_and_watcher():
    for mode, code in (("new_version", "SELECTION_NOT_CURRENT"),
                       ("revoked", "SNAPSHOT_NOT_VISIBLE"),
                       ("expired", "SNAPSHOT_NOT_VISIBLE"),
                       ("watcher_expired", "WATCHER_NOT_CURRENT")):
        stock_source, selection, proof, store, signal_pool = prepared_fixture()
        now = T + 4 * MIN
        if mode == "new_version":
            stock_source.publish(snapshot(version=2, published_at=now,
                                          visible_at=now), now=now)
        elif mode == "revoked":
            stock_source.revoke("synthetic", 1, now=T + 3 * MIN)
        elif mode == "expired":
            now = T + 20 * MIN
        else:
            selection = replace(selection, watcher=watcher(valid_until=T + 3 * MIN))
        with pytest.raises(StockPoolError, match=code):
            prepare(stock_source, selection, proof, store, now=now)
        assert signal_pool.list_open(now) == ()


def test_duplicate_event_and_signal_id_rejected_without_pool_append():
    stock_source, selection, proof, store, signal_pool = prepared_fixture()
    prepare(stock_source, selection, proof, store)
    with pytest.raises(StockPoolError, match="TRIGGER_DUPLICATE"):
        prepare(stock_source, selection, proof, store, signal_id="signal-2")
    other = envelope(code="600001", member="member-2", evidence_time=T + 4 * MIN)
    store.freeze(other)
    stock_source.publish(snapshot(version=2,
                                  members=(MEMBER, PoolMember("SH", "600001", "member-2")),
                                  published_at=T + 4 * MIN,
                                  visible_at=T + 4 * MIN), now=T + 4 * MIN)
    second_selection = stock_source.select("synthetic", market="SH", symbol="600001",
                                           watcher=watcher(), as_of=T + 4 * MIN)
    with pytest.raises(StockPoolError, match="TRIGGER_DUPLICATE"):
        prepare(stock_source, second_selection, other, store,
                trigger_at=T + 4 * MIN, now=T + 4 * MIN, signal_id="signal-1")
    assert signal_pool.list_open(T + 4 * MIN) == ()


def test_commit_fence_rejects_retroactive_publish_and_revoke_across_versions():
    stock_source = StockPoolSource(frozenset({"test-pub"}))
    first = snapshot(published_at=T + 10 * MIN,
                     visible_at=T + 10 * MIN, valid_until=T + 40 * MIN)
    stock_source.publish(first, now=T + 10 * MIN)
    with pytest.raises(StockPoolError, match="PUBLISHED_AT_COMMIT_MISMATCH"):
        stock_source.publish(snapshot(version=2, published_at=T + 2 * MIN,
                                     visible_at=T + 2 * MIN, valid_until=T + 40 * MIN),
                             now=T + 11 * MIN)
    with pytest.raises(StockPoolError, match="REVOKE_BEFORE_PUBLICATION"):
        stock_source.revoke("synthetic", 1, now=T + 3 * MIN)
    with pytest.raises(StockPoolError, match="COMMIT_TIME_NOT_MONOTONIC"):
        stock_source.revoke("synthetic", 1, now=T + 10 * MIN)
    assert stock_source.get("synthetic", 1, as_of=T + 11 * MIN) is first
    assert stock_source.current("synthetic", as_of=T + 11 * MIN) is first
    with pytest.raises(StockPoolError, match="VERSION_UNKNOWN"):
        stock_source.get("synthetic", 2, as_of=T + 11 * MIN)
    stock_source.publish(snapshot(version=2, published_at=T + 12 * MIN,
                                  visible_at=T + 12 * MIN, valid_until=T + 40 * MIN),
                         now=T + 12 * MIN)
    with pytest.raises(StockPoolError, match="COMMIT_TIME_NOT_MONOTONIC"):
        stock_source.revoke("synthetic", 1, now=T + 11 * MIN)
    stock_source.revoke("synthetic", 1, now=T + 13 * MIN)
    assert stock_source.get("synthetic", 1, as_of=T + 12 * MIN) is first
    with pytest.raises(StockPoolError, match="SNAPSHOT_NOT_VISIBLE"):
        stock_source.get("synthetic", 1, as_of=T + 13 * MIN)
    with pytest.raises(StockPoolError, match="COMMIT_TIME_NOT_MONOTONIC"):
        stock_source.publish(snapshot(pool_id="another", published_at=T + 12 * MIN,
                                     visible_at=T + 12 * MIN, valid_until=T + 40 * MIN),
                             now=T + 12 * MIN)


def test_never_backfill_unpublished_version_or_forge_wrong_clock():
    stock_source = source()
    with pytest.raises(StockPoolError, match="PUBLISHED_AT_COMMIT_MISMATCH"):
        stock_source.publish(snapshot(version=2, published_at=T), now=T + 2 * MIN)
    with pytest.raises(StockPoolError, match="REVOKE_BEFORE_PUBLICATION"):
        stock_source.revoke("synthetic", 1, now=T - MIN)
    for when in (datetime(2026, 10, 20, 2, 0),
                 T.astimezone(timezone(timedelta(hours=8)))):
        with pytest.raises(StockPoolError, match="AS_OF_INVALID"):
            stock_source.get("synthetic", 1, as_of=when)
