"""Pure, offline strategy-pool membership with independently attested scan coverage.

This module never reads producer archives as proof of complete scanning. The runner
must keep its signing key and independent observation outside producer control.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Iterable


class BatchStatus(str, Enum):
    """Producer-reported outcome; this is not a trust credential."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class CoverageScope(str, Enum):
    """Independently approved coverage boundary."""

    FULL = "full"
    SCOPED = "scoped"


class EventKind(str, Enum):
    """Auditable changes to one member episode."""

    ENTER = "enter"
    REENTER = "reenter"
    HIT = "hit"
    ABSENT = "absent"
    EXIT = "exit"
    HOLD = "hold"


class DecisionKind(str, Enum):
    """Outcome of ingesting one producer batch."""

    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    UNVERIFIED = "unverified"
    CONFLICT = "conflict"
    STALE = "stale"


def _utc(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware UTC")
    if value.utcoffset().total_seconds() != 0:
        raise ValueError("timestamps must be UTC")


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a nonempty, trimmed string")


@dataclass(frozen=True, order=True)
class SecurityId:
    """Exchange/market-qualified instrument identity, never a bare ticker."""

    market: str
    code: str

    def __post_init__(self) -> None:
        _text(self.market, "market")
        _text(self.code, "code")


@dataclass(frozen=True, order=True)
class StrategyId:
    """Pool-qualified strategy version; pools A and B cannot alias."""

    pool: str
    name: str
    version: str

    def __post_init__(self) -> None:
        for name in ("pool", "name", "version"):
            _text(getattr(self, name), name)


@dataclass(frozen=True)
class Evidence:
    """A direct, time-bounded strategy hit and its non-independent parents."""

    source: str
    reference: str
    observed_at: datetime
    parent_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.source, "source")
        _text(self.reference, "reference")
        _utc(self.observed_at)
        if not isinstance(self.parent_ids, tuple):
            raise ValueError("parent_ids must be immutable")
        for parent in self.parent_ids:
            _text(parent, "parent_id")
        if len(set(self.parent_ids)) != len(self.parent_ids):
            raise ValueError("duplicate parent ids")


@dataclass(frozen=True)
class Hit:
    """One independently observable security selection."""

    security: SecurityId
    evidence: Evidence


@dataclass(frozen=True)
class ScanBatch:
    """Untrusted producer claim until an external runner receipt verifies it."""

    batch_id: str
    strategy: StrategyId
    trade_date: date
    produced_at: datetime
    status: BatchStatus
    scope: CoverageScope
    hits: tuple[Hit, ...]

    def __post_init__(self) -> None:
        _text(self.batch_id, "batch_id")
        if not isinstance(self.trade_date, date) or isinstance(self.trade_date, datetime):
            raise ValueError("trade_date must be a date")
        _utc(self.produced_at)
        if not isinstance(self.status, BatchStatus) or not isinstance(self.scope, CoverageScope):
            raise ValueError("invalid batch status or scope")
        if not isinstance(self.hits, tuple) or not all(isinstance(hit, Hit) for hit in self.hits):
            raise ValueError("hits must be an immutable tuple of Hit")
        if len({hit.security for hit in self.hits}) != len(self.hits):
            raise ValueError("duplicate securities in batch")
        if any(hit.evidence.observed_at > self.produced_at for hit in self.hits):
            raise ValueError("future evidence relative to batch")


@dataclass(frozen=True)
class RunnerObservation:
    """Independent runner's actual visited universe, hits and completion result.

    Must originate in a trusted runner, never from producer JSON or the portal.
    """

    batch_id: str
    strategy: StrategyId
    trade_date: date
    finished_at: datetime
    completed: bool
    visited: frozenset[SecurityId]
    selected: frozenset[SecurityId]

    def __post_init__(self) -> None:
        _text(self.batch_id, "batch_id")
        if not isinstance(self.trade_date, date) or isinstance(self.trade_date, datetime):
            raise ValueError("runner trade_date must be a date")
        _utc(self.finished_at)
        if (not isinstance(self.visited, frozenset) or not isinstance(self.selected, frozenset)
            or not all(isinstance(item, SecurityId) for item in self.visited | self.selected)):
            raise ValueError("runner observations must be frozen sets of security identities")


@dataclass(frozen=True)
class CoverageApproval:
    """A universe approved independently for a specific strategy, date and scope."""

    strategy: StrategyId
    trade_date: date
    scope: CoverageScope
    universe: frozenset[SecurityId]

    def __post_init__(self) -> None:
        if (not isinstance(self.trade_date, date) or isinstance(self.trade_date, datetime)
            or not isinstance(self.scope, CoverageScope) or not isinstance(self.universe, frozenset)):
            raise ValueError("invalid coverage approval")
        if not self.universe or not all(isinstance(item, SecurityId) for item in self.universe):
            raise ValueError("approved universe must contain qualified securities")


def batch_fingerprint(batch: ScanBatch) -> str:
    """Hash the complete producer payload, including evidence and parent lineage."""
    payload = [batch.batch_id, batch.strategy.pool, batch.strategy.name,
               batch.strategy.version, batch.trade_date.isoformat(),
               batch.produced_at.isoformat(), batch.status.value, batch.scope.value,
               sorted((hit.security.market, hit.security.code, hit.evidence.source,
                       hit.evidence.reference, hit.evidence.observed_at.isoformat(),
                       hit.evidence.parent_ids) for hit in batch.hits)]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class CoverageReceipt:
    """Authenticated runner result, not a self-attested producer flag/hash."""

    batch_fingerprint: str
    strategy: StrategyId
    trade_date: date
    finished_at: datetime
    scope: CoverageScope
    covered: frozenset[SecurityId]
    approved_universe: frozenset[SecurityId]
    signature: str


def _receipt_payload(receipt: CoverageReceipt) -> bytes:
    payload = [receipt.batch_fingerprint, receipt.strategy.pool, receipt.strategy.name,
               receipt.strategy.version, receipt.trade_date.isoformat(),
               receipt.finished_at.isoformat(), receipt.scope.value,
               sorted((item.market, item.code) for item in receipt.covered),
               sorted((item.market, item.code) for item in receipt.approved_universe)]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def verify_runner_batch(batch: ScanBatch, observation: RunnerObservation,
                        approval: CoverageApproval, signing_key: bytes) -> CoverageReceipt:
    """Issue a receipt only after comparing external runner observations and approval.

    The signing key and these two inputs are runner-side capabilities. Passing
    producer-supplied versions of either is expressly outside this trust model.
    """
    if not isinstance(signing_key, bytes) or len(signing_key) < 32:
        raise ValueError("independent runner signing key must contain at least 32 bytes")
    if not isinstance(observation, RunnerObservation) or not isinstance(approval, CoverageApproval):
        raise ValueError("independent runner observation and approval required")
    if batch.status is not BatchStatus.COMPLETE or not observation.completed:
        raise ValueError("scan did not complete independently")
    if (batch.batch_id, batch.strategy, batch.trade_date) != (
            observation.batch_id, observation.strategy, observation.trade_date):
        raise ValueError("runner observation does not match producer batch")
    if (batch.strategy, batch.trade_date, batch.scope) != (
            approval.strategy, approval.trade_date, approval.scope):
        raise ValueError("independent approval does not match producer batch")
    if observation.finished_at < batch.produced_at or batch.trade_date > observation.finished_at.date():
        raise ValueError("runner finished before producer batch or trading day")
    if any(hit.evidence.observed_at.date() > batch.trade_date for hit in batch.hits):
        raise ValueError("hit evidence is from a future trading day")
    if not observation.visited or not observation.visited.issubset(approval.universe):
        raise ValueError("visited securities exceed approved coverage")
    if batch.scope is CoverageScope.FULL and observation.visited != approval.universe:
        raise ValueError("full approval requires every independently visited security")
    if not observation.selected.issubset(observation.visited):
        raise ValueError("runner selected securities outside its visited universe")
    if observation.selected != frozenset(hit.security for hit in batch.hits):
        raise ValueError("producer hits differ from runner observed selections")
    unsigned = CoverageReceipt(batch_fingerprint(batch), batch.strategy, batch.trade_date,
                               observation.finished_at, batch.scope, observation.visited,
                               approval.universe, "")
    signature = hmac.new(signing_key, _receipt_payload(unsigned), hashlib.sha256).hexdigest()
    return CoverageReceipt(unsigned.batch_fingerprint, unsigned.strategy, unsigned.trade_date,
                           unsigned.finished_at, unsigned.scope, unsigned.covered,
                           unsigned.approved_universe, signature)


def receipt_is_valid(batch: ScanBatch, receipt: CoverageReceipt | None,
                     signing_key: bytes, as_of: datetime) -> bool:
    """Fail closed unless a matching authenticated receipt is visible at as_of."""
    _utc(as_of)
    if receipt is None or not isinstance(receipt, CoverageReceipt) or not isinstance(signing_key, bytes):
        return False
    if (batch.status is not BatchStatus.COMPLETE or batch.produced_at > as_of
            or batch.trade_date > as_of.date()):
        return False
    if (len(signing_key) < 32 or receipt.batch_fingerprint != batch_fingerprint(batch) or
            receipt.strategy != batch.strategy or receipt.trade_date != batch.trade_date or
            receipt.scope is not batch.scope or receipt.finished_at < batch.produced_at or
            receipt.finished_at > as_of or not receipt.covered or
            not receipt.covered.issubset(receipt.approved_universe) or
            (receipt.scope is CoverageScope.FULL and
             receipt.covered != receipt.approved_universe) or
            not frozenset(hit.security for hit in batch.hits).issubset(receipt.covered)):
        return False
    expected = hmac.new(signing_key, _receipt_payload(receipt), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, receipt.signature)


@dataclass(frozen=True)
class MembershipEvent:
    """One immutable event scoped to a strategy, security and episode."""

    kind: EventKind
    strategy: StrategyId
    security: SecurityId
    episode: int
    trade_date: date
    batch_id: str
    at: datetime
    evidence: Evidence | None = None


@dataclass(frozen=True)
class Member:
    """Current state of one strategy/security member episode."""

    strategy: StrategyId
    security: SecurityId
    episode: int
    active: bool
    consecutive_absences: int
    held: bool
    last_hit_date: date
    last_evidence: Evidence


@dataclass(frozen=True)
class BatchDecision:
    """Visible outcome including unverified hits without promoting them to members."""

    batch_id: str
    strategy: StrategyId
    kind: DecisionKind
    reason: str
    unverified_hits: tuple[Hit, ...] = ()


@dataclass(frozen=True)
class PoolState:
    """Immutable reducer state, including the same-day accepted-batch ledger."""

    members: tuple[Member, ...] = ()
    events: tuple[MembershipEvent, ...] = ()
    accepted: tuple[tuple[StrategyId, str, date, str], ...] = ()
    last_dates: tuple[tuple[StrategyId, str, date], ...] = ()
    decisions: tuple[BatchDecision, ...] = ()


@dataclass(frozen=True)
class PoolPolicy:
    """Exit is disabled unless isolated callers explicitly enable it."""

    auto_exit_enabled: bool = False
    absence_threshold: int = 3

    def __post_init__(self) -> None:
        if not isinstance(self.absence_threshold, int) or self.absence_threshold < 1:
            raise ValueError("absence_threshold must be positive")


def reduce_batch(state: PoolState, batch: ScanBatch, receipt: CoverageReceipt | None,
                 signing_key: bytes, as_of: datetime,
                 policy: PoolPolicy = PoolPolicy()) -> PoolState:
    """Apply only a verified, nonconflicting new trading-day coverage boundary."""
    _utc(as_of)
    def decided(kind: DecisionKind, reason: str, unverified: bool = False) -> PoolState:
        decision = BatchDecision(batch.batch_id, batch.strategy, kind, reason,
                                 batch.hits if unverified else ())
        return PoolState(state.members, state.events, state.accepted, state.last_dates,
                         state.decisions + (decision,))

    if (batch.produced_at > as_of or batch.trade_date > as_of.date() or
            any(hit.evidence.observed_at > as_of for hit in batch.hits)):
        return decided(DecisionKind.UNVERIFIED, "future batch or evidence", True)
    fingerprint = batch_fingerprint(batch)
    previous = next((entry for entry in state.accepted
                     if entry[0] == batch.strategy and entry[1] == batch.batch_id), None)
    if previous is not None:
        if previous[3] == fingerprint:
            return state
        return decided(DecisionKind.CONFLICT, "batch id reused with different payload")
    if not receipt_is_valid(batch, receipt, signing_key, as_of):
        return decided(DecisionKind.UNVERIFIED, "missing or invalid independent receipt", True)
    markets = {item.market for item in receipt.covered}
    last = {(strategy, market): day for strategy, market, day in state.last_dates}
    for market in markets:
        prior_day = last.get((batch.strategy, market))
        if prior_day is not None and batch.trade_date < prior_day:
            return decided(DecisionKind.STALE, "older than an accepted trading day")
        if prior_day == batch.trade_date:
            return decided(DecisionKind.CONFLICT, "same-day rerun is frozen")
    members = {(member.strategy, member.security): member for member in state.members}
    events = list(state.events)
    selected = {hit.security: hit for hit in batch.hits}
    for security in sorted(receipt.covered):
        key = (batch.strategy, security)
        member = members.get(key)
        hit = selected.get(security)
        if hit is not None:
            if member is None or not member.active:
                episode = 1 if member is None else member.episode + 1
                kind = EventKind.ENTER if member is None else EventKind.REENTER
                members[key] = Member(batch.strategy, security, episode, True, 0,
                                      False, batch.trade_date, hit.evidence)
            else:
                episode = member.episode
                kind = EventKind.HIT
                members[key] = Member(batch.strategy, security, episode, True, 0,
                                      member.held, batch.trade_date, hit.evidence)
            events.append(MembershipEvent(kind, batch.strategy, security, episode,
                                          batch.trade_date, batch.batch_id,
                                          receipt.finished_at, hit.evidence))
        elif member is not None and member.active:
            absences = member.consecutive_absences + 1
            should_exit = (policy.auto_exit_enabled and not member.held and
                           absences >= policy.absence_threshold)
            members[key] = Member(member.strategy, security, member.episode,
                                  not should_exit, absences, member.held,
                                  member.last_hit_date, member.last_evidence)
            events.append(MembershipEvent(EventKind.EXIT if should_exit else EventKind.ABSENT,
                                          batch.strategy, security, member.episode,
                                          batch.trade_date, batch.batch_id,
                                          receipt.finished_at))
    updated_last = dict(last)
    for market in markets:
        updated_last[(batch.strategy, market)] = batch.trade_date
    return PoolState(tuple(sorted(members.values(), key=lambda m: (m.strategy, m.security))),
                     tuple(events),
                     state.accepted + ((batch.strategy, batch.batch_id, batch.trade_date,
                                        fingerprint),),
                     tuple(sorted((strategy, market, day)
                                  for (strategy, market), day in updated_last.items())),
                     state.decisions + (BatchDecision(batch.batch_id, batch.strategy,
                                                       DecisionKind.ACCEPTED,
                                                       "independently verified"),))


def set_manual_hold(state: PoolState, strategy: StrategyId, security: SecurityId,
                    at: datetime) -> PoolState:
    """Set a manual hold on an active member; unhold needs a separate approved contract."""
    _utc(at)
    updated = []
    events = state.events
    found = False
    for member in state.members:
        if (member.strategy, member.security) == (strategy, security):
            if not member.active:
                raise ValueError("cannot hold an exited episode")
            found = True
            updated.append(Member(strategy, security, member.episode, True,
                                  member.consecutive_absences, True,
                                  member.last_hit_date, member.last_evidence))
            if not member.held:
                events += (MembershipEvent(EventKind.HOLD, strategy, security,
                                           member.episode, at.date(), "manual", at),)
        else:
            updated.append(member)
    if not found:
        raise ValueError("member not found")
    return PoolState(tuple(updated), events, state.accepted, state.last_dates, state.decisions)


@dataclass(frozen=True)
class SecurityProjection:
    """Read-only cross-strategy view; parent IDs never count as independent hits."""

    security: SecurityId
    memberships: tuple[Member, ...]
    independent_strategies: tuple[StrategyId, ...]
    unverified_hits: tuple[tuple[StrategyId, Hit], ...]


def project_security(security: SecurityId, states: Iterable[PoolState],
                     unverified_batches: Iterable[ScanBatch] = ()) -> SecurityProjection:
    """Combine verified memberships without conflating markets, pools or parents."""
    states_tuple = tuple(states)
    members = tuple(member for state in states_tuple for member in state.members
                    if member.security == security and member.active)
    independent = tuple(sorted({member.strategy for member in members}))
    unverified = tuple(
        [(decision.strategy, hit) for state in states_tuple for decision in state.decisions
         if decision.kind is DecisionKind.UNVERIFIED
         for hit in decision.unverified_hits if hit.security == security] +
        [(batch.strategy, hit) for batch in unverified_batches
         for hit in batch.hits if hit.security == security])
    return SecurityProjection(security, members, independent, unverified)
