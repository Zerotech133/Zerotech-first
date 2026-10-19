"""XuChu watch camp signal pool: producer-attributed signal registry.

Offline append-only, memory-only projection of watch-camp signals. Every
entry binds a frozen evidence envelope identity from an injected
``EvidenceStore``; nothing here is executable, persistent, networked, or a
trade/delivery authority. Entries carry observation attribution only — no
order, fill, pnl, or authorization fields exist on this structure.

Contract (per 2026-10-19 watch signal pool design, section 2):

- ``SignalPoolError(ValueError)``: fail-closed, all rejections are
  module-level error-code constants.
- ``SignalPoolEntry``: frozen dataclass; state machine
  OPEN -> CLAIMED -> ACCEPTED; REVOKED is enterable from any state and
  irreversible; ``epoch`` only ever increases (on revoke).
- Dedupe is judged per producer domain:
  * same (event_id, evidence_hash, producer_agent_id) -> idempotent return
    of the existing entry;
  * same event_id, different evidence_hash, same producer -> SIGNAL_CONFLICT;
  * same event_id, same evidence_hash, different producer -> new entry with
    ``co_signed`` set to the first reporter's signal_id; entries are never
    merged or overwritten.
- Lease claiming: only OPEN entries can claim; a live lease blocks with
  CLAIM_HELD; expired leases are lazily recycled back to OPEN on any read
  or claim (no background task).
- ``mark_accepted``: only a CLAIMED entry transitions to ACCEPTED and binds
  an upstream acceptance receipt (outcome ACCEPTED_VERIFIED). The receipt is
  a plain frozen dataclass of basic-type summary fields; nothing is imported
  from ``route_outbox``.
- All datetimes must be timezone-aware UTC; naive datetimes are rejected
  (same discipline as ``agent_reporting._utc``).

Design note on "frozen and not revoked": ``EvidenceStore`` is append-only
and has no revocation primitive, so a frozen envelope identity found in the
store is by construction un-revoked; the read-only lookup
``_find_frozen`` is the single place to extend should the store ever grow a
revocation concept.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from quant_backtest.watch_dispatch.agent_reporting import AgentIdentity
from quant_backtest.watch_dispatch.canonical import utc_text
from quant_backtest.watch_dispatch.evidence_store import EvidenceStore

__all__ = [
    "ACCEPTED_VERIFIED_OUTCOME",
    "AcceptanceReceipt",
    "SignalPool",
    "SignalPoolDraft",
    "SignalPoolEntry",
    "SignalPoolError",
    "STATUS_ACCEPTED",
    "STATUS_CLAIMED",
    "STATUS_OPEN",
    "STATUS_REVOKED",
]

_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_KINDS = frozenset(("ANOMALY", "HEALTH"))

# Internal state machine values (entry.status).
STATUS_OPEN = "OPEN"
STATUS_CLAIMED = "CLAIMED"
STATUS_ACCEPTED = "ACCEPTED"
STATUS_REVOKED = "REVOKED"
_STATUSES = frozenset((STATUS_OPEN, STATUS_CLAIMED, STATUS_ACCEPTED, STATUS_REVOKED))

# Only outcome a bound upstream receipt may carry (literal mirror of the
# route_outbox constant; deliberately NOT imported to keep this module free
# of route_outbox coupling).
ACCEPTED_VERIFIED_OUTCOME = "ACCEPTED_VERIFIED"

# Fail-closed error codes.
AGENT_NOT_CURRENT = "AGENT_NOT_CURRENT"
PRODUCER_ROLE_INVALID = "PRODUCER_ROLE_INVALID"
EVIDENCE_NOT_FROZEN = "EVIDENCE_NOT_FROZEN"
SIGNAL_CONFLICT = "SIGNAL_CONFLICT"
CLAIM_HELD = "CLAIM_HELD"
CLAIM_LEASE_INVALID = "CLAIM_LEASE_INVALID"
CLAIM_STATE_INVALID = "CLAIM_STATE_INVALID"
ACCEPT_STATE_INVALID = "ACCEPT_STATE_INVALID"
RECEIPT_INVALID = "RECEIPT_INVALID"
RECEIPT_OUTCOME_INVALID = "RECEIPT_OUTCOME_INVALID"
REVOKE_FINAL = "REVOKE_FINAL"
EPOCH_INVALID = "EPOCH_INVALID"
SIGNAL_UNKNOWN = "SIGNAL_UNKNOWN"
SIGNAL_ID_TAKEN = "SIGNAL_ID_TAKEN"
STORE_MISMATCH = "STORE_MISMATCH"
STORE_INCOMPATIBLE = "STORE_INCOMPATIBLE"
DRAFT_INVALID = "DRAFT_INVALID"


class SignalPoolError(ValueError):
    """Fail-closed signal pool violation; message is an error-code constant."""


def _label(value: object, field: str) -> None:
    if type(value) is not str or _LABEL.fullmatch(value) is None:
        raise SignalPoolError(field + "_INVALID")


def _require_utc(value: object, field: str) -> None:
    """Reject naive or non-UTC datetimes (agent_reporting._utc discipline)."""
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise SignalPoolError(field + "_INVALID")
    utc_text(value)  # type: ignore[arg-type]


def _sha_hex(value: object, field: str) -> None:
    if type(value) is not str or _SHA.fullmatch(value) is None:
        raise SignalPoolError(field + "_INVALID")


def _find_frozen(store: Any, event_id: str, evidence_hash: str,
                 evidence_version: int) -> Any | None:
    """Read-only lookup of a frozen envelope by (event_id, hash, version).

    Uses the store's internal version map without mutating anything. Raises
    STORE_INCOMPATIBLE for stores that do not expose the expected read-only
    structure. "Frozen and not revoked" holds because the store is
    append-only with no revocation primitive (see module docstring).
    """
    versions_map = getattr(store, "_versions", None)
    if type(versions_map) is not dict:
        raise SignalPoolError(STORE_INCOMPATIBLE)
    for versions in versions_map.values():
        if type(versions) is not dict:
            continue
        envelope = versions.get(evidence_version)
        if envelope is None:
            continue
        if (getattr(envelope, "event_id", None) == event_id
                and getattr(envelope, "envelope_hash", None) == evidence_hash
                and getattr(envelope, "evidence_version", None) == evidence_version):
            return envelope
    return None


@dataclass(frozen=True)
class SignalPoolDraft:
    """Producer-supplied append draft; producer attribution is never carried
    here — it is derived exclusively from the verified ``AgentIdentity``."""

    signal_id: str
    report_kind: str
    event_id: str
    evidence_hash: str
    evidence_version: int

    def __post_init__(self) -> None:
        _label(self.signal_id, "SIGNAL_ID")
        if self.report_kind not in _KINDS or type(self.report_kind) is not str:
            raise SignalPoolError("REPORT_KIND_INVALID")
        _label(self.event_id, "EVENT_ID")
        _sha_hex(self.evidence_hash, "EVIDENCE_HASH")
        if type(self.evidence_version) is not int or self.evidence_version < 1:
            raise SignalPoolError("EVIDENCE_VERSION_INVALID")


@dataclass(frozen=True)
class AcceptanceReceipt:
    """Plain upstream-acceptance receipt summary; basic types only, no
    route_outbox import. Binds the ACCEPTED_VERIFIED outcome reference."""

    route: str
    attempt_id: str
    external_ref: str
    outcome: str = ACCEPTED_VERIFIED_OUTCOME

    def __post_init__(self) -> None:
        _label(self.route, "RECEIPT_ROUTE")
        _label(self.attempt_id, "RECEIPT_ATTEMPT_ID")
        _label(self.external_ref, "RECEIPT_EXTERNAL_REF")
        if self.outcome != ACCEPTED_VERIFIED_OUTCOME or type(self.outcome) is not str:
            raise SignalPoolError(RECEIPT_OUTCOME_INVALID)


@dataclass(frozen=True)
class SignalPoolEntry:
    """Immutable watch-signal pool entry; observation attribution only.

    Carries no order, fill, pnl, or authorization field and permits no
    execution semantics of any kind.
    """

    signal_id: str
    producer_agent_id: str
    report_kind: str
    event_id: str
    evidence_hash: str
    evidence_version: int
    created_at: datetime
    co_signed: str | None = None
    status: str = STATUS_OPEN
    epoch: int = 0

    @property
    def production_permitted(self) -> bool:
        return False

    @property
    def notification_permitted(self) -> bool:
        return False

    @property
    def trading_permitted(self) -> bool:
        return False

    def __post_init__(self) -> None:
        _label(self.signal_id, "SIGNAL_ID")
        _label(self.producer_agent_id, "PRODUCER_AGENT_ID")
        if self.report_kind not in _KINDS or type(self.report_kind) is not str:
            raise SignalPoolError("REPORT_KIND_INVALID")
        _label(self.event_id, "EVENT_ID")
        _sha_hex(self.evidence_hash, "EVIDENCE_HASH")
        if type(self.evidence_version) is not int or self.evidence_version < 1:
            raise SignalPoolError("EVIDENCE_VERSION_INVALID")
        _require_utc(self.created_at, "CREATED_AT")
        if self.co_signed is not None:
            _label(self.co_signed, "CO_SIGNED")
            if self.co_signed == self.signal_id:
                raise SignalPoolError("CO_SIGNED_INVALID")
        if self.status not in _STATUSES or type(self.status) is not str:
            raise SignalPoolError("STATUS_INVALID")
        if type(self.epoch) is not int or self.epoch < 0:
            raise SignalPoolError("EPOCH_INVALID")


def _transition(old: SignalPoolEntry, new: SignalPoolEntry) -> SignalPoolEntry:
    """Guard every in-place mutation: epoch may never regress."""
    if type(new) is not SignalPoolEntry or new.epoch < old.epoch:
        raise SignalPoolError(EPOCH_INVALID)
    return new


class SignalPool:
    """Offline memory registry of watch-camp signals; not a delivery or
    trade authority. Constructed with an injected ``EvidenceStore``; every
    append re-presents the store and must match the injected one."""

    def __init__(self, store: EvidenceStore) -> None:
        self._store = store
        self._entries: dict[str, SignalPoolEntry] = {}
        self._leases: dict[str, datetime] = {}
        self._receipts: dict[str, AcceptanceReceipt] = {}

    @property
    def production_permitted(self) -> bool:
        return False

    @property
    def notification_permitted(self) -> bool:
        return False

    @property
    def trading_permitted(self) -> bool:
        return False

    # ------------------------------------------------------------------
    # append
    # ------------------------------------------------------------------
    def append(self, draft: SignalPoolDraft, *, producer: AgentIdentity,
               store: EvidenceStore, now: datetime) -> SignalPoolEntry:
        """Validate and append one signal draft; dedupe per producer domain.

        Order of fail-closed gates: time validity, draft shape, producer
        role/currency, store binding, frozen-evidence binding, dedupe
        verdict, co-sign attribution, insertion.
        """
        _require_utc(now, "NOW")
        if type(draft) is not SignalPoolDraft:
            raise SignalPoolError(DRAFT_INVALID)
        if store is not self._store:
            raise SignalPoolError(STORE_MISMATCH)
        if type(producer) is not AgentIdentity or producer.role != "WATCH":
            raise SignalPoolError(PRODUCER_ROLE_INVALID)
        if not producer.current(now):
            raise SignalPoolError(AGENT_NOT_CURRENT)
        if _find_frozen(store, draft.event_id, draft.evidence_hash,
                        draft.evidence_version) is None:
            raise SignalPoolError(EVIDENCE_NOT_FROZEN)
        # Dedupe verdict — producer-scoped domain first.
        for entry in self._entries.values():
            if (entry.producer_agent_id == producer.agent_id
                    and entry.event_id == draft.event_id):
                if entry.evidence_hash == draft.evidence_hash:
                    return entry  # idempotent: exact re-append
                raise SignalPoolError(SIGNAL_CONFLICT)
        # Cross-producer co-sign: first reporter of the same evidence wins.
        co_signed: str | None = None
        for entry in self._entries.values():
            if (entry.event_id == draft.event_id
                    and entry.evidence_hash == draft.evidence_hash):
                co_signed = entry.signal_id
                break
        if draft.signal_id in self._entries:
            raise SignalPoolError(SIGNAL_ID_TAKEN)
        entry = SignalPoolEntry(
            signal_id=draft.signal_id,
            producer_agent_id=producer.agent_id,
            report_kind=draft.report_kind,
            event_id=draft.event_id,
            evidence_hash=draft.evidence_hash,
            evidence_version=draft.evidence_version,
            created_at=now,
            co_signed=co_signed,
            status=STATUS_OPEN,
            epoch=0,
        )
        self._entries[draft.signal_id] = entry
        return entry

    # ------------------------------------------------------------------
    # lease claiming
    # ------------------------------------------------------------------
    def claim(self, signal_id: str, *, lease_until: datetime,
              now: datetime) -> SignalPoolEntry:
        """Take an exclusive claim lease on an OPEN entry."""
        _require_utc(now, "NOW")
        _require_utc(lease_until, "LEASE_UNTIL")
        if lease_until <= now:
            raise SignalPoolError(CLAIM_LEASE_INVALID)
        self._recycle(now)
        entry = self._entries.get(signal_id)
        if entry is None:
            raise SignalPoolError(SIGNAL_UNKNOWN)
        if entry.status == STATUS_OPEN:
            claimed = _transition(entry, replace(entry, status=STATUS_CLAIMED))
            self._entries[signal_id] = claimed
            self._leases[signal_id] = lease_until
            return claimed
        if entry.status == STATUS_CLAIMED:
            raise SignalPoolError(CLAIM_HELD)
        raise SignalPoolError(CLAIM_STATE_INVALID)

    # ------------------------------------------------------------------
    # acceptance
    # ------------------------------------------------------------------
    def mark_accepted(self, signal_id: str, *,
                      receipt: AcceptanceReceipt) -> SignalPoolEntry:
        """Close the loop CLAIMED -> ACCEPTED with an upstream receipt."""
        if type(receipt) is not AcceptanceReceipt:
            raise SignalPoolError(RECEIPT_INVALID)
        entry = self._entries.get(signal_id)
        if entry is None:
            raise SignalPoolError(SIGNAL_UNKNOWN)
        if entry.status != STATUS_CLAIMED:
            raise SignalPoolError(ACCEPT_STATE_INVALID)
        accepted = _transition(entry, replace(entry, status=STATUS_ACCEPTED))
        self._entries[signal_id] = accepted
        self._leases.pop(signal_id, None)
        self._receipts[signal_id] = receipt
        return accepted

    def receipt(self, signal_id: str) -> AcceptanceReceipt | None:
        """Read-only view of the bound acceptance receipt, if any."""
        return self._receipts.get(signal_id)

    # ------------------------------------------------------------------
    # revoke
    # ------------------------------------------------------------------
    def revoke(self, signal_id: str, *, now: datetime) -> SignalPoolEntry:
        """Revoke from any state; irreversible; epoch strictly increases."""
        _require_utc(now, "NOW")
        entry = self._entries.get(signal_id)
        if entry is None:
            raise SignalPoolError(SIGNAL_UNKNOWN)
        if entry.status == STATUS_REVOKED:
            raise SignalPoolError(REVOKE_FINAL)
        revoked = _transition(
            entry, replace(entry, status=STATUS_REVOKED, epoch=entry.epoch + 1))
        self._entries[signal_id] = revoked
        self._leases.pop(signal_id, None)
        return revoked

    # ------------------------------------------------------------------
    # read-only views
    # ------------------------------------------------------------------
    def get(self, signal_id: str, *, now: datetime | None = None) -> SignalPoolEntry | None:
        """Read one entry; expired leases are recycled when ``now`` is given."""
        if now is not None:
            _require_utc(now, "NOW")
            self._recycle(now)
        return self._entries.get(signal_id)

    def list_open(self, now: datetime) -> tuple[SignalPoolEntry, ...]:
        """Snapshot of OPEN entries with expired leases lazily recycled."""
        _require_utc(now, "NOW")
        self._recycle(now)
        return tuple(entry for entry in self._entries.values()
                     if entry.status == STATUS_OPEN)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _recycle(self, now: datetime) -> None:
        """Lazily return CLAIMED entries whose lease has passed to OPEN."""
        for signal_id, lease_until in list(self._leases.items()):
            if now > lease_until:
                entry = self._entries.get(signal_id)
                if entry is not None and entry.status == STATUS_CLAIMED:
                    self._entries[signal_id] = _transition(
                        entry, replace(entry, status=STATUS_OPEN))
                self._leases.pop(signal_id, None)
