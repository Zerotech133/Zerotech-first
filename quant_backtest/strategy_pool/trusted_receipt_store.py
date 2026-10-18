"""Fail-closed, read-only adapter for externally sealed strategy-pool receipts.

This module never issues a receipt, chooses the approved universe, signs a hash,
opens a producer path, or discovers a signing key. Its caller must inject ports
backed by independently operated, sealed authorities. A caller-provided port
cannot prove its own independence: even a successful result is OFFLINE_QUALIFIED,
never production admission. No portal or production service imports this adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from .core import (
    CoverageApproval,
    CoverageReceipt,
    DecisionKind,
    PoolPolicy,
    PoolState,
    RunnerObservation,
    ScanBatch,
    StrategyId,
    parse_artifact_hits,
    receipt_is_valid,
    reduce_batch,
)


@dataclass(frozen=True)
class ApprovalCensus:
    """Externally sealed, complete strategy inventory for one trading day.

    A subset returned by a producer is not a census. The authority implementing
    authenticate_census must verify the sealed inventory and its completeness,
    independently of the batch producer and the receipt signing authority.
    """

    trade_date: date
    sealed_at: datetime
    identity: str
    expected_strategies: frozenset[StrategyId]
    approvals: tuple[CoverageApproval, ...]


@dataclass(frozen=True)
class SealedBatchMaterial:
    """External receipt, runner observation and original sealed artifact bytes."""

    receipt: CoverageReceipt
    observation: RunnerObservation
    raw_bytes: bytes


@dataclass(frozen=True)
class ActiveBatchHead:
    """Authenticated ledger head including a durable anti-replay revision."""

    strategy: StrategyId
    trade_date: date
    batch_id: str
    revision: str
    recorded_at: datetime


class TrustedReceiptPorts(Protocol):
    """Read-only ports implemented by independent custody and approval systems.

    authenticate_material must authenticate receipt/observation provenance and
    immutable byte custody, not merely rehash producer JSON. verification_key
    must originate outside producer control. The active batch lookup must use a
    durable authoritative head as visible at as_of, including revocations; a
    filesystem mtime, process exit code, or producer batch ID is not a head.
    """

    def load_census(self, trade_date: date, as_of: datetime) -> ApprovalCensus:
        """Load the independently sealed full approval inventory."""

    def authenticate_census(self, census: ApprovalCensus) -> bool:
        """Check external approval authority and complete-inventory seal."""

    def authenticate_approval(self, approval: CoverageApproval, as_of: datetime) -> bool:
        """Verify current approval validity, version floor and revocation externally."""

    def load_material(self, batch: ScanBatch, as_of: datetime) -> SealedBatchMaterial:
        """Load externally issued receipt and original bytes, not producer JSON."""

    def authenticate_material(self, material: SealedBatchMaterial) -> bool:
        """Check external issuance and storage custody independently."""

    def load_active_head(self, strategy: StrategyId, trade_date: date,
                         as_of: datetime) -> ActiveBatchHead:
        """Read a durable active head at the requested time."""

    def authenticate_active_head(self, head: ActiveBatchHead) -> bool:
        """Verify ledger signature, revocation and current revision externally."""

    def verification_key(self) -> bytes:
        """Return the independent runner's verification secret for offline use."""


@dataclass(frozen=True)
class OfflineQualification:
    """An offline-only outcome; no value of this DTO grants production access."""

    status: str
    reason_code: str
    state: PoolState
    production_permitted: bool = False


def qualify_trusted_day(state: PoolState, batches: tuple[ScanBatch, ...],
                        as_of: datetime, ports: TrustedReceiptPorts | None
                        ) -> OfflineQualification:
    """Atomically qualify one complete externally approved day in memory.

    Absence and membership events are committed only when *every* approved
    strategy's batch and custody evidence pass. Failed lookups, invalid input,
    replay, missing ports and authority exceptions leave state untouched. The
    policy is hard-coded to disable automatic exits regardless of caller input.
    """
    denied = OfflineQualification("SOURCE_UNVERIFIED", "TRUSTED_SOURCE_UNAVAILABLE", state)
    if not isinstance(state, PoolState) or not isinstance(batches, tuple) or not batches:
        return denied
    if (not isinstance(as_of, datetime) or as_of.tzinfo is None or
            as_of.utcoffset() is None or as_of.utcoffset().total_seconds() != 0):
        return denied
    if ports is None or not all(isinstance(batch, ScanBatch) for batch in batches):
        return denied
    trade_date = batches[0].trade_date
    strategies = tuple(batch.strategy for batch in batches)
    if (trade_date > as_of.date() or any(batch.trade_date != trade_date for batch in batches)
            or len(set(strategies)) != len(strategies)
            or any(strategy in strategies and previous_day > trade_date
                   for strategy, _market, previous_day in state.last_dates)):
        return denied
    try:
        census = ports.load_census(trade_date, as_of)
        if not isinstance(census, ApprovalCensus) or ports.authenticate_census(census) is not True:
            return denied
        if (census.trade_date != trade_date or not isinstance(census.sealed_at, datetime)
                or census.sealed_at.tzinfo is None or census.sealed_at.utcoffset() is None
                or census.sealed_at.utcoffset().total_seconds() != 0
                or census.sealed_at > as_of or not isinstance(census.identity, str)
                or not census.identity.strip() or not isinstance(census.expected_strategies, frozenset)
                or not census.expected_strategies or not isinstance(census.approvals, tuple)
                or any(not isinstance(item, StrategyId) for item in census.expected_strategies)
                or any(not isinstance(item, CoverageApproval) for item in census.approvals)
                or set(strategies) != census.expected_strategies):
            return denied
        approvals = {approval.strategy: approval for approval in census.approvals}
        if (len(approvals) != len(census.approvals) or
                set(approvals) != census.expected_strategies or
                any(approval.trade_date != trade_date for approval in census.approvals) or
                any(ports.authenticate_approval(approval, as_of) is not True
                    for approval in census.approvals)):
            return denied
        key = ports.verification_key()
        if not isinstance(key, bytes) or len(key) < 32:
            return denied
        verified: list[tuple[ScanBatch, SealedBatchMaterial, CoverageApproval,
                             tuple]] = []
        for batch in batches:
            head = ports.load_active_head(batch.strategy, trade_date, as_of)
            if (not isinstance(head, ActiveBatchHead) or
                    ports.authenticate_active_head(head) is not True or
                    head.strategy != batch.strategy or head.trade_date != trade_date or
                    head.batch_id != batch.batch_id or not isinstance(head.revision, str) or
                    not head.revision.strip() or not isinstance(head.recorded_at, datetime) or
                    head.recorded_at.tzinfo is None or head.recorded_at.utcoffset() is None or
                    head.recorded_at.utcoffset().total_seconds() != 0 or
                    head.recorded_at > as_of):
                return denied
            material = ports.load_material(batch, as_of)
            if (not isinstance(material, SealedBatchMaterial) or
                    ports.authenticate_material(material) is not True or
                    not isinstance(material.raw_bytes, bytes)):
                return denied
            parsed = parse_artifact_hits(material.raw_bytes)
            approval = approvals[batch.strategy]
            if not receipt_is_valid(batch, material.receipt, key, as_of,
                                    material.raw_bytes, parsed, material.observation, approval):
                return denied
            verified.append((batch, material, approval, parsed))
        next_state = state
        for batch, material, approval, parsed in verified:
            updated = reduce_batch(next_state, batch, material.receipt, key, as_of,
                                   PoolPolicy(auto_exit_enabled=False),
                                   raw_bytes=material.raw_bytes, parsed_hits=parsed,
                                   observation=material.observation, approval=approval)
            if updated is not next_state and (
                    not updated.decisions or updated.decisions[-1].kind is not DecisionKind.ACCEPTED):
                return denied
            next_state = updated
        return OfflineQualification("OFFLINE_QUALIFIED", "EXTERNAL_RECEIPTS_VERIFIED",
                                    next_state)
    except Exception:
        # A malformed or failing external authority cannot become a trusted hit.
        return denied
