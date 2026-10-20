"""Explicit, memory-only bridge from a watch trigger draft to SignalPool.

The caller owns the trigger decision and supplies the pool, evidence store,
registered producer identity, and clock. This module neither resolves an agent
registry nor attests a snapshot manifest; it grants no delivery or trade right.
"""
from __future__ import annotations

import re
from datetime import datetime

from quant_backtest.watch_dispatch.agent_reporting import AgentIdentity
from quant_backtest.watch_dispatch.canonical import utc_text
from quant_backtest.watch_dispatch.contracts import EvidenceEnvelope
from quant_backtest.watch_dispatch.evidence_store import EvidenceStore
from quant_backtest.watch_dispatch.signal_pool import (
    SignalPool, SignalPoolDraft, SignalPoolEntry,
)
from quant_backtest.watch_dispatch.stock_pool_source import WatchSignalIntent

_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_MARKET = re.compile(r"[A-Z][A-Z0-9_]{0,15}\Z")
_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.:-]{0,31}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")


class StockTriggerPoolAdapterError(ValueError):
    """Fail-closed rejection before a draft is handed to the signal pool."""


def _require_utc(value: object, field: str) -> None:
    """Reject naive, non-UTC, and non-datetime clock values."""
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise StockTriggerPoolAdapterError(field + "_INVALID")
    try:
        utc_text(value)
    except ValueError as exc:
        raise StockTriggerPoolAdapterError(field + "_INVALID") from exc


def _matches(value: object, pattern: re.Pattern[str]) -> bool:
    return type(value) is str and pattern.fullmatch(value) is not None


def _frozen_evidence(store: EvidenceStore, draft: SignalPoolDraft) -> EvidenceEnvelope:
    """Find a frozen envelope through the same in-memory map used by SignalPool.

    This is not an external source receipt: the store can attest only to its
    own frozen in-memory object. Never accept a lookalike envelope from intent.
    """
    versions_map = getattr(store, "_versions", None)
    if type(versions_map) is not dict:
        raise StockTriggerPoolAdapterError("STORE_INCOMPATIBLE")
    matches: list[EvidenceEnvelope] = []
    for versions in versions_map.values():
        if type(versions) is not dict:
            raise StockTriggerPoolAdapterError("STORE_INCOMPATIBLE")
        envelope = versions.get(draft.evidence_version)
        if (type(envelope) is EvidenceEnvelope
                and envelope.event_id == draft.event_id
                and envelope.envelope_hash == draft.evidence_hash
                and envelope.evidence_version == draft.evidence_version
                and store.get_exact(envelope) is envelope):
            matches.append(envelope)
    if len(matches) != 1:
        raise StockTriggerPoolAdapterError("EVIDENCE_NOT_FROZEN")
    return matches[0]


def append_trigger_intent(
    intent: WatchSignalIntent, *, pool: SignalPool, store: EvidenceStore,
    producer: AgentIdentity, now: datetime,
) -> SignalPoolEntry:
    """Validate an existing offline draft then append a real in-memory entry.

    The injected producer is the only attribution passed to SignalPool. An
    intent's producer_agent_id and source_manifest are claims, not independent
    authority. A valid caller-supplied identity is still only an offline
    identity: this method performs no registry lookup, delivery, or trading.
    """
    _require_utc(now, "NOW")
    if type(intent) is not WatchSignalIntent or type(intent.draft) is not SignalPoolDraft:
        raise StockTriggerPoolAdapterError("INTENT_INVALID")
    if intent.status != "OFFLINE_DRAFT_ONLY" or type(intent.status) is not str:
        raise StockTriggerPoolAdapterError("INTENT_STATUS_INVALID")
    if type(pool) is not SignalPool or type(store) is not EvidenceStore:
        raise StockTriggerPoolAdapterError("POOL_STORE_INVALID")
    if getattr(pool, "_store", None) is not store:
        raise StockTriggerPoolAdapterError("STORE_MISMATCH")
    if (type(producer) is not AgentIdentity or producer.role != "WATCH"
            or not _matches(intent.producer_agent_id, _LABEL)
            or intent.producer_agent_id != producer.agent_id):
        raise StockTriggerPoolAdapterError("PRODUCER_MISMATCH")
    if (not _matches(intent.pool_id, _LABEL)
            or type(intent.pool_version) is not int or intent.pool_version < 1
            or not _matches(intent.market, _MARKET)
            or not _matches(intent.symbol, _CODE)
            or not _matches(intent.membership_id, _LABEL)
            or not _matches(intent.rule_id, _LABEL)
            or type(intent.rule_version) is not int or intent.rule_version < 1
            or not _matches(intent.source_manifest, _SHA)):
        raise StockTriggerPoolAdapterError("INTENT_METADATA_INVALID")
    _require_utc(intent.trigger_at, "TRIGGER_AT")
    _require_utc(intent.prepared_at, "PREPARED_AT")
    if not intent.trigger_at <= intent.prepared_at <= now:
        raise StockTriggerPoolAdapterError("INTENT_TIME_INVALID")
    if (not producer.current(intent.trigger_at)
            or not producer.current(intent.prepared_at)
            or not producer.current(now)):
        raise StockTriggerPoolAdapterError("PRODUCER_NOT_CURRENT")
    draft = intent.draft
    if draft.report_kind != "ANOMALY":
        raise StockTriggerPoolAdapterError("REPORT_KIND_INVALID")
    envelope = _frozen_evidence(store, draft)
    try:
        envelope.validate(now=now)
    except ValueError as exc:
        raise StockTriggerPoolAdapterError("EVIDENCE_INVALID") from exc
    if (envelope.market != intent.market or envelope.code != intent.symbol
            or envelope.source_membership_id != intent.membership_id
            or envelope.rule_id != intent.rule_id
            or envelope.rule_version != intent.rule_version):
        raise StockTriggerPoolAdapterError("EVIDENCE_INTENT_MISMATCH")
    if not (envelope.source_as_of <= envelope.visible_at <= envelope.detected_at
            <= envelope.freeze_at <= intent.trigger_at < envelope.valid_until):
        raise StockTriggerPoolAdapterError("EVIDENCE_NOT_VISIBLE_AT_TRIGGER")
    return pool.append(draft, producer=producer, store=store, now=now)
