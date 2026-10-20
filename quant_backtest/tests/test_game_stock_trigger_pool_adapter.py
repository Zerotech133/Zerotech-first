"""Synthetic, offline-only tests for the stock trigger -> signal pool bridge."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from quant_backtest.game_wiring.stock_trigger_pool_adapter import (
    StockTriggerPoolAdapterError, append_trigger_intent,
)
from quant_backtest.watch_dispatch.agent_reporting import AgentIdentity
from quant_backtest.watch_dispatch.contracts import EvidenceEnvelope, freeze_offline
from quant_backtest.watch_dispatch.evidence_store import EvidenceStore
from quant_backtest.watch_dispatch.signal_pool import (
    SignalPool, SignalPoolError, STATUS_OPEN,
)
from quant_backtest.watch_dispatch.stock_pool_source import (
    PoolMember, StockPoolSnapshot, StockPoolSource, WatchSignalIntent,
)

T = datetime(2026, 10, 20, 2, 0, tzinfo=timezone.utc)
MINUTE = timedelta(minutes=1)


def make_evidence(*, pool: str = "B", event_id: str = "event-1",
                  observed: str = "1.10") -> EvidenceEnvelope:
    """Build a self-consistent synthetic observation without a live source."""
    return freeze_offline(
        schema_version=1, evidence_version=1, previous_version_hash=None,
        pool=pool, tenant_or_owner_id="owner-1", market="SH", code="600000",
        source_membership_id="member-1", source_revision="revision-1",
        rule_id="rule-test", rule_version=1, comparator="GE", threshold="1.00",
        unit="ratio", window="5m", observed_value=observed,
        baseline_value="1.00", raw_ref="raw:test", raw_digest="b" * 64,
        source_id="test-source", calendar_id="test-calendar",
        source_as_of=T + 2 * MINUTE, visible_at=T + 2 * MINUTE,
        detected_at=T + 2 * MINUTE, freeze_at=T + 2 * MINUTE,
        valid_until=T + 15 * MINUTE, facts=("synthetic",),
        hypotheses=("hypothesis",), supporting_refs=("raw:test",),
        contradicting_refs=(), confirmation_condition="synthetic condition",
        invalidation_condition="synthetic invalidate", event_id=event_id,
    )


def make_fixture(*, signal_id: str = "signal-1",
                 evidence: EvidenceEnvelope | None = None,
                 frozen: bool = True) -> tuple[WatchSignalIntent, SignalPool,
                                                EvidenceStore, AgentIdentity]:
    """Prepare a real draft through StockPoolSource's offline API."""
    source = StockPoolSource(frozenset({"test-pub"}))
    snapshot = StockPoolSnapshot(
        pool_id="synthetic", version=1, publisher="test-pub",
        members=(PoolMember("SH", "600000", "member-1"),),
        published_at=T, visible_at=T + MINUTE, valid_until=T + 20 * MINUTE,
        source_manifest="a" * 64,
    )
    source.publish(snapshot, now=T)
    producer = AgentIdentity("watch-1", "WATCH", "test-register",
                             T - MINUTE, T + 30 * MINUTE)
    selection = source.select("synthetic", market="SH", symbol="600000",
                              watcher=producer, as_of=T + MINUTE)
    store = EvidenceStore()
    proof = evidence if evidence is not None else make_evidence()
    store.freeze(proof)
    intent = source.prepare_trigger(selection, triggered=True, evidence=proof,
                                    store=store, signal_id=signal_id,
                                    trigger_at=T + 3 * MINUTE,
                                    now=T + 3 * MINUTE)
    if not frozen:
        store = EvidenceStore()
    return intent, SignalPool(store), store, producer


def append(intent: WatchSignalIntent, signal_pool: SignalPool,
           store: EvidenceStore, producer: AgentIdentity,
           *, now: datetime = T + 3 * MINUTE):
    """Invoke only the offline adapter with explicitly injected dependencies."""
    return append_trigger_intent(intent, pool=signal_pool, store=store,
                                 producer=producer, now=now)


def test_explicit_append_creates_one_real_non_executable_open_entry():
    intent, signal_pool, store, producer = make_fixture()
    assert signal_pool.get(intent.draft.signal_id) is None
    entry = append(intent, signal_pool, store, producer)
    assert entry is signal_pool.get(intent.draft.signal_id)
    assert (entry.signal_id, entry.event_id, entry.producer_agent_id) == (
        "signal-1", "event-1", producer.agent_id)
    assert (entry.evidence_hash, entry.evidence_version) == (
        intent.draft.evidence_hash, intent.draft.evidence_version)
    assert entry.report_kind == "ANOMALY" and entry.created_at == T + 3 * MINUTE
    assert entry.status == STATUS_OPEN and entry.epoch == 0 and entry.co_signed is None
    assert not entry.production_permitted and not entry.notification_permitted
    assert not entry.trading_permitted
    assert not signal_pool.production_permitted and not store.production_permitted


def test_repeat_is_idempotent_without_second_entry():
    intent, signal_pool, store, producer = make_fixture()
    first = append(intent, signal_pool, store, producer)
    second = append(intent, signal_pool, store, producer, now=T + 4 * MINUTE)
    assert second is first
    assert signal_pool.list_open(T + 4 * MINUTE) == (first,)


@pytest.mark.parametrize("mode,code", [
    ("other_producer", "PRODUCER_MISMATCH"),
    ("wrong_role", "PRODUCER_MISMATCH"),
    ("forged_claim", "PRODUCER_MISMATCH"),
    ("expired", "PRODUCER_NOT_CURRENT"),
    ("future", "INTENT_TIME_INVALID"),
    ("before_registration", "PRODUCER_NOT_CURRENT"),
    ("non_utc", "NOW_INVALID"),
    ("wrong_store", "STORE_MISMATCH"),
    ("unfrozen", "EVIDENCE_NOT_FROZEN"),
    ("wrong_market", "EVIDENCE_INTENT_MISMATCH"),
    ("wrong_member", "EVIDENCE_INTENT_MISMATCH"),
    ("wrong_rule", "EVIDENCE_INTENT_MISMATCH"),
    ("wrong_event", "EVIDENCE_NOT_FROZEN"),
    ("wrong_hash", "EVIDENCE_NOT_FROZEN"),
    ("wrong_kind", "REPORT_KIND_INVALID"),
    ("wrong_status", "INTENT_STATUS_INVALID"),
    ("late_trigger", "EVIDENCE_NOT_VISIBLE_AT_TRIGGER"),
    ("bad_manifest", "INTENT_METADATA_INVALID"),
])
def test_rejection_never_appends_or_performs_production_work(mode: str, code: str):
    intent, signal_pool, store, producer = make_fixture()
    now = T + 3 * MINUTE
    if mode == "other_producer":
        producer = replace(producer, agent_id="watch-2")
    elif mode == "wrong_role":
        producer = replace(producer, role="QUANT_SECTOR")
    elif mode == "forged_claim":
        intent = replace(intent, producer_agent_id="watch-2")
    elif mode == "expired":
        now = T + 30 * MINUTE
    elif mode == "future":
        now = T + 2 * MINUTE
    elif mode == "before_registration":
        producer = replace(producer, registered_at=T + 3 * MINUTE + timedelta(seconds=1))
    elif mode == "non_utc":
        now = datetime(2026, 10, 20, 2, 3)
    elif mode == "wrong_store":
        store = EvidenceStore()
    elif mode == "unfrozen":
        store = EvidenceStore()
        signal_pool = SignalPool(store)
    elif mode == "wrong_market":
        intent = replace(intent, market="SZ")
    elif mode == "wrong_member":
        intent = replace(intent, membership_id="member-2")
    elif mode == "wrong_rule":
        intent = replace(intent, rule_version=2)
    elif mode == "wrong_event":
        intent = replace(intent, draft=replace(intent.draft, event_id="event-2"))
    elif mode == "wrong_hash":
        intent = replace(intent, draft=replace(intent.draft, evidence_hash="f" * 64))
    elif mode == "wrong_kind":
        intent = replace(intent, draft=replace(intent.draft, report_kind="HEALTH"))
    elif mode == "wrong_status":
        intent = replace(intent, status="READY_PRODUCTION")
    elif mode == "late_trigger":
        intent = replace(intent, trigger_at=T + MINUTE)
    else:
        intent = replace(intent, source_manifest="untrusted")
    with pytest.raises(StockTriggerPoolAdapterError, match=f"^{code}$"):
        append(intent, signal_pool, store, producer, now=now)
    assert signal_pool.get("signal-1") is None
    assert not signal_pool.production_permitted and not signal_pool.notification_permitted
    assert not signal_pool.trading_permitted


def test_conflict_and_signal_id_collision_are_enforced_by_real_pool():
    intent, signal_pool, store, producer = make_fixture()
    first = append(intent, signal_pool, store, producer)
    different = make_evidence(pool="A", observed="1.20")
    store.freeze(different)
    conflict = replace(intent, draft=replace(intent.draft, signal_id="signal-2",
                                              evidence_hash=different.envelope_hash))
    with pytest.raises(SignalPoolError, match="^SIGNAL_CONFLICT$"):
        append(conflict, signal_pool, store, producer)
    assert signal_pool.get("signal-2") is None
    other = make_evidence(event_id="event-2")
    store.freeze(other)
    taken = replace(intent, draft=replace(intent.draft, event_id="event-2",
                                           evidence_hash=other.envelope_hash))
    with pytest.raises(SignalPoolError, match="^SIGNAL_ID_TAKEN$"):
        append(taken, signal_pool, store, producer)
    assert signal_pool.list_open(T + 3 * MINUTE) == (first,)


def test_manifest_claim_is_not_promoted_to_source_authority():
    intent, signal_pool, store, producer = make_fixture()
    forged_manifest = replace(intent, source_manifest="f" * 64)
    entry = append(forged_manifest, signal_pool, store, producer)
    assert entry.status == STATUS_OPEN and not entry.production_permitted
    assert not entry.notification_permitted and not entry.trading_permitted
    assert not hasattr(entry, "source_manifest")
