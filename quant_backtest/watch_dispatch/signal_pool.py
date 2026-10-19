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
- Legacy ``mark_accepted``: only a CLAIMED entry transitions to ACCEPTED and
  binds an upstream receipt (outcome ACCEPTED_VERIFIED), but the caller supplies
  the binding port: this is LEGACY_UNTRUSTED, never production authority.
  Controlled-scope pools are offline preparation only; they refuse every
  acceptance until an independently owned dynamic trust root is available.
  The receipt is a plain frozen dataclass of basic-type summary fields;
  nothing is imported from ``route_outbox``.
- All datetimes must be timezone-aware UTC; naive datetimes are rejected
  (same discipline as ``agent_reporting._utc``).

Design note on "frozen and not revoked": ``EvidenceStore`` is append-only
and has no revocation primitive, so a frozen envelope identity found in the
store is by construction un-revoked; the read-only lookup
``_find_frozen`` is the single place to extend should the store ever grow a
revocation concept.

Behavior clarifications (recorded for product review):
- Cross-producer dedupe: the same event_id with a different evidence_hash
  from a different producer yields fully independent entries — dedupe and
  conflict verdicts are judged strictly inside each producer's own domain,
  and cross-producer entries are never merged or overwritten. This
  behavior is frozen by adversarial tests and awaits a product ruling
  before any cross-producer merge semantics may be introduced.
- Upstream non-acceptance outcomes: after an upstream result of UNKNOWN or
  DEFINITELY_REJECTED, an entry returns to OPEN through lazy expired-lease
  recycling on the next read or claim (see ``_recycle``); there is no
  explicit release API by design.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Protocol

from quant_backtest.watch_dispatch.agent_reporting import AgentIdentity
from quant_backtest.watch_dispatch.canonical import utc_text
from quant_backtest.watch_dispatch.evidence_store import EvidenceStore

__all__ = [
    "ACCEPTED_VERIFIED_OUTCOME",
    "AcceptanceReceipt",
    "AttemptBinding",
    "ControlledSignalScope",
    "ReceiptBindingPort",
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
RECEIPT_VERIFIER_REQUIRED = "RECEIPT_VERIFIER_REQUIRED"
RECEIPT_ATTEMPT_UNKNOWN = "RECEIPT_ATTEMPT_UNKNOWN"
RECEIPT_OUTCOME_MISMATCH = "RECEIPT_OUTCOME_MISMATCH"
RECEIPT_REF_MISMATCH = "RECEIPT_REF_MISMATCH"
RECEIPT_ROUTE_MISMATCH = "RECEIPT_ROUTE_MISMATCH"
RECEIPT_IDENTITY_MISMATCH = "RECEIPT_IDENTITY_MISMATCH"
UTC_REQUIRED = "UTC_REQUIRED"
REVOKE_FINAL = "REVOKE_FINAL"
EPOCH_INVALID = "EPOCH_INVALID"
SIGNAL_UNKNOWN = "SIGNAL_UNKNOWN"
SIGNAL_ID_TAKEN = "SIGNAL_ID_TAKEN"
STORE_MISMATCH = "STORE_MISMATCH"
STORE_INCOMPATIBLE = "STORE_INCOMPATIBLE"
DRAFT_INVALID = "DRAFT_INVALID"
CONTROLLED_SCOPE_INVALID = "CONTROLLED_SCOPE_INVALID"
CONTROLLED_SCOPE_MISMATCH = "CONTROLLED_SCOPE_MISMATCH"
CONTROLLED_BINDING_PORT_MISMATCH = "CONTROLLED_BINDING_PORT_MISMATCH"
CONTROLLED_AUTHORITY_UNAVAILABLE = "CONTROLLED_AUTHORITY_UNAVAILABLE"
LEGACY_UNTRUSTED = "LEGACY_UNTRUSTED"
CONTROLLED_PENDING_ROOT = "CONTROLLED_PENDING_ROOT"


class SignalPoolError(ValueError):
    """Fail-closed signal pool violation; message is an error-code constant."""


def _label(value: object, field: str) -> None:
    if type(value) is not str or _LABEL.fullmatch(value) is None:
        raise SignalPoolError(field + "_INVALID")


def _require_utc(value: object, field: str) -> None:
    """Reject non-datetime, naive, and non-zero-offset (non-UTC) values.

    ``canonical.utc_text`` raises a bare ``ValueError("UTC_REQUIRED")`` for
    a non-zero UTC offset; this wrapper converts every such rejection into
    ``SignalPoolError`` so no entry path can leak a foreign error type.
    ``canonical.py`` is a frozen module and is never modified here.
    """
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise SignalPoolError(field + "_INVALID")
    try:
        utc_text(value)  # type: ignore[arg-type]
    except ValueError as exc:  # bare ValueError("UTC_REQUIRED") from canonical
        raise SignalPoolError(UTC_REQUIRED) from exc


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
class ControlledSignalScope:
    """Exact, single-signal identity of an offline controlled pool.

    This is a selector, not an independent source of authorization.
    """

    signal_id: str
    producer_agent_id: str
    report_kind: str
    event_id: str
    evidence_hash: str
    evidence_version: int

    def __post_init__(self) -> None:
        _label(self.signal_id, "SIGNAL_ID")
        _label(self.producer_agent_id, "PRODUCER_AGENT_ID")
        if type(self.report_kind) is not str or self.report_kind not in _KINDS:
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
class AttemptBinding:
    """Read-only outbox attempt evidence and its frozen-envelope identity."""

    attempt_id: str
    external_message_id: str
    outcome: str
    route: str
    event_id: str
    evidence_hash: str
    evidence_version: int


class ReceiptBindingPort(Protocol):
    """Look up a settled attempt without mutating either outbox or pool."""

    def binding_for(self, attempt_id: str) -> AttemptBinding | None:
        """Return the attempt's receipt binding, or None when unavailable."""


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

    def __init__(self, store: EvidenceStore, *,
                 controlled_scope: ControlledSignalScope | None = None,
                 acceptance_binding: ReceiptBindingPort | None = None) -> None:
        if (controlled_scope is None) != (acceptance_binding is None):
            raise SignalPoolError(CONTROLLED_SCOPE_INVALID)
        if controlled_scope is not None:
            if (type(controlled_scope) is not ControlledSignalScope
                    or not callable(getattr(acceptance_binding, "binding_for", None))):
                raise SignalPoolError(CONTROLLED_SCOPE_INVALID)
        self._store = store
        self._controlled_scope = controlled_scope
        self._acceptance_binding = acceptance_binding
        self._entries: dict[str, SignalPoolEntry] = {}
        self._leases: dict[str, datetime] = {}
        self._receipts: dict[str, AcceptanceReceipt] = {}

    @property
    def acceptance_mode(self) -> str:
        """Disclosure label, not an authorization or production permission."""
        if self._controlled_scope is None:
            return LEGACY_UNTRUSTED
        return CONTROLLED_PENDING_ROOT

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
    def mark_accepted(self, signal_id: str, *, receipt: AcceptanceReceipt,
                      binding_port: ReceiptBindingPort | None = None,
                      now: datetime | None = None) -> SignalPoolEntry:
        """Legacy acceptance or controlled preparation (always fails closed).

        Legacy caller-injected bindings are untrusted. In controlled mode the
        constructor fixes the sole binding port and exact signal identity; a
        per-call replacement is refused without invoking it. Even a matching
        port cannot establish a trusted finish time: the current adapter has
        constructor-frozen _now, and RouteReceipt has no finished_at. A
        caller-supplied ``now`` is likewise not an independent time authority.
        No controlled ACCEPTED mutation is permitted by this offline slice.
        """
        if self._controlled_scope is not None:
            if binding_port is not None and binding_port is not self._acceptance_binding:
                raise SignalPoolError(CONTROLLED_BINDING_PORT_MISMATCH)
            scope = self._controlled_scope
            entry = self._entries.get(signal_id)
            if (signal_id != scope.signal_id or entry is None
                    or (entry.signal_id, entry.producer_agent_id, entry.report_kind,
                        entry.event_id, entry.evidence_hash, entry.evidence_version)
                    != (scope.signal_id, scope.producer_agent_id, scope.report_kind,
                        scope.event_id, scope.evidence_hash, scope.evidence_version)):
                raise SignalPoolError(CONTROLLED_SCOPE_MISMATCH)
            if now is not None:
                _require_utc(now, "NOW")
            # No read from the fixed adapter is necessary: it cannot prove a
            # dynamic, independently observed finish inside this pool's lease.
            # Do not turn its plausible output into an acceptance authority.
            raise SignalPoolError(CONTROLLED_AUTHORITY_UNAVAILABLE)
        if binding_port is None or not callable(getattr(binding_port, "binding_for", None)):
            raise SignalPoolError(RECEIPT_VERIFIER_REQUIRED)
        if type(receipt) is not AcceptanceReceipt:
            raise SignalPoolError(RECEIPT_INVALID)
        entry = self._entries.get(signal_id)
        if entry is None:
            raise SignalPoolError(SIGNAL_UNKNOWN)
        if entry.status != STATUS_CLAIMED:
            raise SignalPoolError(ACCEPT_STATE_INVALID)
        try:
            binding = binding_port.binding_for(receipt.attempt_id)
        except Exception as exc:
            raise SignalPoolError(RECEIPT_ATTEMPT_UNKNOWN) from exc
        if type(binding) is not AttemptBinding or binding.attempt_id != receipt.attempt_id:
            raise SignalPoolError(RECEIPT_ATTEMPT_UNKNOWN)
        if binding.outcome != ACCEPTED_VERIFIED_OUTCOME:
            raise SignalPoolError(RECEIPT_OUTCOME_MISMATCH)
        if binding.external_message_id != receipt.external_ref:
            raise SignalPoolError(RECEIPT_REF_MISMATCH)
        if binding.route != receipt.route or binding.route != "QUANT_UPSTREAM_REPORT":
            raise SignalPoolError(RECEIPT_ROUTE_MISMATCH)
        if (binding.event_id, binding.evidence_hash, binding.evidence_version) != (
                entry.event_id, entry.evidence_hash, entry.evidence_version):
            raise SignalPoolError(RECEIPT_IDENTITY_MISMATCH)
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
