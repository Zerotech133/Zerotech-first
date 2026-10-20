"""G0/G1 offline stock-pool snapshots and XuChu's explicitly triggered draft adapter.

This is an in-memory, caller-clocked research/shadow model, not a publisher
identity service, selection algorithm, trigger evaluator, or production authority.
No method sends reports, inserts into SignalPool, or executes orders.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from quant_backtest.watch_dispatch.agent_reporting import AgentIdentity
from quant_backtest.watch_dispatch.canonical import utc_text
from quant_backtest.watch_dispatch.contracts import EvidenceEnvelope
from quant_backtest.watch_dispatch.evidence_store import EvidenceStore
from quant_backtest.watch_dispatch.signal_pool import SignalPoolDraft

_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_MARKET = re.compile(r"[A-Z][A-Z0-9_]{0,15}\Z")
_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.:-]{0,31}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
RESEARCH_ONLY = "RESEARCH_ONLY_NOT_PRODUCTION"
OFFLINE_SHADOW = "OFFLINE_SHADOW"
BLOCK_CUTOVER = "BLOCK_CUTOVER"


class StockPoolError(ValueError):
    """Fail-closed stock-pool contract violation, with stable code message."""


def _match(value: object, pattern: re.Pattern[str], field: str) -> None:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise StockPoolError(field + "_INVALID")


def _utc(value: object, field: str) -> None:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise StockPoolError(field + "_INVALID")
    try:
        utc_text(value)
    except ValueError as exc:
        raise StockPoolError(field + "_INVALID") from exc


@dataclass(frozen=True)
class PoolMember:
    """One exact market/security and immutable upstream membership reference."""

    market: str
    symbol: str
    membership_id: str

    def __post_init__(self) -> None:
        _match(self.market, _MARKET, "MARKET")
        _match(self.symbol, _CODE, "SYMBOL")
        _match(self.membership_id, _LABEL, "MEMBERSHIP_ID")


@dataclass(frozen=True)
class StockPoolSnapshot:
    """Immutable published version; source manifest is a reference, not proof."""

    pool_id: str
    version: int
    publisher: str
    members: tuple[PoolMember, ...]
    published_at: datetime
    visible_at: datetime
    valid_until: datetime
    source_manifest: str
    status: str = OFFLINE_SHADOW

    def __post_init__(self) -> None:
        _match(self.pool_id, _LABEL, "POOL_ID")
        _match(self.publisher, _LABEL, "PUBLISHER")
        _match(self.source_manifest, _SHA, "SOURCE_MANIFEST")
        if type(self.version) is not int or self.version < 1:
            raise StockPoolError("VERSION_INVALID")
        if type(self.members) is not tuple or not self.members or any(
            type(member) is not PoolMember for member in self.members
        ):
            raise StockPoolError("MEMBERS_INVALID")
        identities = [(member.market, member.symbol) for member in self.members]
        if len(set(identities)) != len(identities):
            raise StockPoolError("MEMBERS_DUPLICATE")
        if len({member.membership_id for member in self.members}) != len(self.members):
            raise StockPoolError("MEMBERSHIP_DUPLICATE")
        if type(self.status) is not str or self.status not in (OFFLINE_SHADOW, RESEARCH_ONLY):
            raise StockPoolError("STATUS_INVALID")
        for name in ("published_at", "visible_at", "valid_until"):
            _utc(getattr(self, name), name.upper())
        if not self.published_at <= self.visible_at < self.valid_until:
            raise StockPoolError("TIME_ORDER_INVALID")

    @property
    def production_permitted(self) -> bool:
        return False

    @property
    def notification_permitted(self) -> bool:
        return False

    @property
    def trading_permitted(self) -> bool:
        return False

    @property
    def cutover_status(self) -> str:
        return BLOCK_CUTOVER


@dataclass(frozen=True)
class WatchSelection:
    """XuChu's read-only choice pinned to a frozen pool version and member."""

    snapshot: StockPoolSnapshot
    member: PoolMember
    watcher: AgentIdentity
    selected_at: datetime

    def __post_init__(self) -> None:
        if (type(self.snapshot) is not StockPoolSnapshot
                or type(self.member) is not PoolMember
                or self.member not in self.snapshot.members
                or type(self.watcher) is not AgentIdentity
                or self.watcher.role != "WATCH"):
            raise StockPoolError("SELECTION_INVALID")
        _utc(self.selected_at, "SELECTED_AT")
        if not self.watcher.current(self.selected_at):
            raise StockPoolError("WATCHER_NOT_CURRENT")


@dataclass(frozen=True)
class WatchSignalIntent:
    """Non-executable adapter output, never itself a signal-pool entry."""

    draft: SignalPoolDraft
    pool_id: str
    pool_version: int
    market: str
    symbol: str
    membership_id: str
    rule_id: str
    rule_version: int
    trigger_at: datetime
    prepared_at: datetime
    producer_agent_id: str
    source_manifest: str
    status: str = "OFFLINE_DRAFT_ONLY"

    @property
    def production_permitted(self) -> bool:
        return False

    @property
    def notification_permitted(self) -> bool:
        return False

    @property
    def trading_permitted(self) -> bool:
        return False

    @property
    def cutover_status(self) -> str:
        return BLOCK_CUTOVER


class StockPoolSource:
    """Append-only offline publisher and read-only as-of selector.

    A constructor allowlist is local test configuration, not a trusted publisher
    directory. Historical replay uses caller-supplied UTC and cannot attest to
    real publication, market data, or permissions.
    """

    def __init__(self, allowed_publishers: frozenset[str]) -> None:
        if (type(allowed_publishers) is not frozenset or not allowed_publishers
                or any(type(item) is not str or _LABEL.fullmatch(item) is None
                       for item in allowed_publishers)):
            raise StockPoolError("PUBLISHER_ALLOWLIST_INVALID")
        self._allowed_publishers = allowed_publishers
        self._snapshots: dict[str, list[StockPoolSnapshot]] = {}
        self._revocations: dict[tuple[str, int], datetime] = {}
        self._prepared_events: set[tuple[str, str]] = set()
        self._prepared_signal_ids: set[str] = set()
        # Local, caller-clocked monotonic fence; NOT a trusted production clock.
        self._last_commit_at: datetime | None = None

    def publish(self, snapshot: StockPoolSnapshot, *, now: datetime) -> StockPoolSnapshot:
        """Append a new version; no retroactive insertion or version mutation."""
        _utc(now, "NOW")
        if type(snapshot) is not StockPoolSnapshot:
            raise StockPoolError("SNAPSHOT_INVALID")
        if snapshot.publisher not in self._allowed_publishers:
            raise StockPoolError("PUBLISHER_UNAUTHORIZED")
        if snapshot.published_at != now:
            raise StockPoolError("PUBLISHED_AT_COMMIT_MISMATCH")
        if self._last_commit_at is not None and now <= self._last_commit_at:
            raise StockPoolError("COMMIT_TIME_NOT_MONOTONIC")
        history = self._snapshots.get(snapshot.pool_id, [])
        if history:
            last = history[-1]
            if snapshot.version != last.version + 1 or snapshot.published_at <= last.published_at:
                raise StockPoolError("VERSION_ORDER_INVALID")
            if last.status == RESEARCH_ONLY and snapshot.status != RESEARCH_ONLY:
                raise StockPoolError("RESEARCH_CUTOVER_BLOCKED")
        elif snapshot.version != 1:
            raise StockPoolError("VERSION_ORDER_INVALID")
        if not history:
            self._snapshots[snapshot.pool_id] = [snapshot]
        else:
            history.append(snapshot)
        self._last_commit_at = now
        return snapshot

    def revoke(self, pool_id: str, version: int, *, now: datetime) -> None:
        """Record irreversible effective-time tombstone, without rewriting a snapshot."""
        _utc(now, "NOW")
        _match(pool_id, _LABEL, "POOL_ID")
        snapshot = next((s for s in self._snapshots.get(pool_id, ()) if s.version == version), None)
        if snapshot is None:
            raise StockPoolError("VERSION_UNKNOWN")
        key = (pool_id, version)
        if key in self._revocations:
            raise StockPoolError("ALREADY_REVOKED")
        if now < snapshot.published_at:
            raise StockPoolError("REVOKE_BEFORE_PUBLICATION")
        if self._last_commit_at is not None and now <= self._last_commit_at:
            raise StockPoolError("COMMIT_TIME_NOT_MONOTONIC")
        self._revocations[key] = now
        self._last_commit_at = now

    def get(self, pool_id: str, version: int, *, as_of: datetime) -> StockPoolSnapshot:
        """Read exact version only if published, visible, live, and not revoked."""
        _utc(as_of, "AS_OF")
        _match(pool_id, _LABEL, "POOL_ID")
        if type(version) is not int or version < 1:
            raise StockPoolError("VERSION_INVALID")
        snapshot = next((s for s in self._snapshots.get(pool_id, ()) if s.version == version), None)
        if snapshot is None:
            raise StockPoolError("VERSION_UNKNOWN")
        revoked_at = self._revocations.get((pool_id, version))
        if (as_of < snapshot.published_at or as_of < snapshot.visible_at
                or as_of >= snapshot.valid_until
                or (revoked_at is not None and as_of >= revoked_at)):
            raise StockPoolError("SNAPSHOT_NOT_VISIBLE")
        return snapshot

    def current(self, pool_id: str, *, as_of: datetime) -> StockPoolSnapshot:
        """Resolve latest published/visible version; never fall back from revoked latest."""
        _utc(as_of, "AS_OF")
        _match(pool_id, _LABEL, "POOL_ID")
        candidates = [s for s in self._snapshots.get(pool_id, ())
                      if s.published_at <= as_of and s.visible_at <= as_of]
        if not candidates:
            raise StockPoolError("SNAPSHOT_NOT_VISIBLE")
        return self.get(pool_id, candidates[-1].version, as_of=as_of)

    def select(self, pool_id: str, *, market: str, symbol: str,
               watcher: AgentIdentity, as_of: datetime) -> WatchSelection:
        """Record one exact-security choice; never produce a trigger or signal."""
        snapshot = self.current(pool_id, as_of=as_of)
        if type(watcher) is not AgentIdentity or watcher.role != "WATCH":
            raise StockPoolError("WATCHER_ROLE_INVALID")
        if not watcher.current(as_of):
            raise StockPoolError("WATCHER_NOT_CURRENT")
        member = next((m for m in snapshot.members
                       if m.market == market and m.symbol == symbol), None)
        if member is None:
            raise StockPoolError("MEMBER_NOT_FOUND")
        return WatchSelection(snapshot, member, watcher, as_of)

    def prepare_trigger(self, selection: WatchSelection, *, triggered: bool,
                        evidence: EvidenceEnvelope | None, store: EvidenceStore,
                        signal_id: str, trigger_at: datetime,
                        now: datetime) -> WatchSignalIntent:
        """Prepare a draft only on explicit trigger and matching frozen evidence.

        The caller owns trigger evaluation. An envelope's comparator is not a
        business rule interpreter; evidence presence alone is not a trigger.
        This adapter deliberately never invokes SignalPool.append.
        """
        _utc(trigger_at, "TRIGGER_AT")
        _utc(now, "NOW")
        if type(selection) is not WatchSelection:
            raise StockPoolError("SELECTION_INVALID")
        if triggered is not True:
            raise StockPoolError("TRIGGER_NOT_CONFIRMED")
        snapshot = self.current(selection.snapshot.pool_id, as_of=now)
        if (snapshot is not selection.snapshot or selection.member not in snapshot.members
                or selection.selected_at > trigger_at or trigger_at > now
                or not (snapshot.visible_at <= trigger_at < snapshot.valid_until)
                or (self._revocations.get((snapshot.pool_id, snapshot.version)) is not None
                    and trigger_at >= self._revocations[(snapshot.pool_id, snapshot.version)])):
            raise StockPoolError("SELECTION_NOT_CURRENT")
        if snapshot.status != OFFLINE_SHADOW:
            raise StockPoolError("RESEARCH_CUTOVER_BLOCKED")
        if (type(selection.watcher) is not AgentIdentity
                or selection.watcher.role != "WATCH"
                or not selection.watcher.current(trigger_at)
                or not selection.watcher.current(now)):
            raise StockPoolError("WATCHER_NOT_CURRENT")
        if type(evidence) is not EvidenceEnvelope or type(store) is not EvidenceStore:
            raise StockPoolError("EVIDENCE_NOT_FROZEN")
        frozen = store.get_exact(evidence)
        if frozen is not evidence:
            raise StockPoolError("EVIDENCE_NOT_FROZEN")
        try:
            evidence.validate(now=now)
        except ValueError as exc:
            raise StockPoolError("EVIDENCE_INVALID") from exc
        if (evidence.market != selection.member.market
                or evidence.code != selection.member.symbol
                or evidence.source_membership_id != selection.member.membership_id):
            raise StockPoolError("EVIDENCE_MEMBER_MISMATCH")
        # Sampling may precede selection; only the information's visibility
        # and its frozen envelope must precede the asserted trigger instant.
        if (not (evidence.source_as_of <= evidence.visible_at
                 <= evidence.detected_at <= evidence.freeze_at <= trigger_at)
                or not (trigger_at < evidence.valid_until)):
            raise StockPoolError("EVIDENCE_NOT_VISIBLE_AT_TRIGGER")
        draft = SignalPoolDraft(signal_id=signal_id, report_kind="ANOMALY",
                                event_id=evidence.event_id,
                                evidence_hash=evidence.envelope_hash,
                                evidence_version=evidence.evidence_version)
        event_key = (selection.watcher.agent_id, evidence.event_id)
        if event_key in self._prepared_events or signal_id in self._prepared_signal_ids:
            raise StockPoolError("TRIGGER_DUPLICATE")
        intent = WatchSignalIntent(draft, snapshot.pool_id, snapshot.version,
                                   selection.member.market, selection.member.symbol,
                                   selection.member.membership_id, evidence.rule_id,
                                   evidence.rule_version, trigger_at, now,
                                   selection.watcher.agent_id, snapshot.source_manifest)
        self._prepared_events.add(event_key)
        self._prepared_signal_ids.add(signal_id)
        return intent
