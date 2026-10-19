"""W-Q3 explicit recovery of lease-expired suspended dispatch intents.

Offline only; no network, no execution semantics. This module closes the
W-Q1 follow-up ("分发账租约过期悬挂 intent 无自动回收"): once a claim lease
has fully elapsed, the intent hangs in CLAIMED forever unless an operator
explicitly reclaims it (``reclaim_expired``). ``recover_expired_intents``
is the batch entry: it scans one route ledger, explicitly reclaims every
lease-expired suspended intent through that same operator API, and appends
one immutable audit record per recovery to an append-only log.

Contract:
- Append-only: recovery records are only ever appended; the ledger attempt
  history, the control epoch and the bounded attempt budget are untouched
  (reclaim semantics ratified in W-Q1, commit 6faf2565).
- Idempotent: dedupe is by intent id; an already-recovered intent is never
  reprocessed, so repeated scans append no duplicate records.
- Auditable: every record carries the intent id, route, receipt id, expired
  lease time, recovery time, reason code and the recovering actor.
- Zero behavior change: no existing ledger or gate code path is modified;
  unexpired, live-lease, completed, cancelled and revoked intents are never
  touched.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, FrozenSet, List, Optional, Tuple

from quant_backtest.quant_sector.dispatch_ledger import (
    _ROUTES as DISPATCH_ROUTES,
    CLAIMED,
    DispatchAttempt,
    DispatchIntent,
    DispatchLedger,
)
from quant_backtest.quant_sector.upstream_inbox import (
    canonical_bytes,
    utc_text,
)

RECORD_KIND = "LEASE_RECOVERY"
REASON_LEASE_EXPIRED = "LEASE_EXPIRED_RECLAIM"

_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_RECORD_FIELDS = frozenset((
    "attempt_no", "intent_id", "lease_until", "reason_code", "receipt_id",
    "recovered_at", "recovered_by", "record_kind", "route",
))


class LeaseRecoveryError(ValueError):
    """Fail-closed lease recovery violation; message is an error-code constant."""


def _label(value: object, field: str) -> None:
    if type(value) is not str or _LABEL.fullmatch(value) is None:
        raise LeaseRecoveryError(field + "_INVALID")


def _utc(value: object, field: str) -> None:
    if (type(value) is not datetime or value.tzinfo is None
            or value.utcoffset() != timedelta(0)):
        raise LeaseRecoveryError(field + "_INVALID")


@dataclass(frozen=True)
class LeaseRecoveryRecord:
    """One append-only recovery record for a lease-expired suspended intent.

    Never rewritten once appended; the canonical JSON line is the audit
    artifact. ``recovered_by`` is the executing operator attribution;
    ``receipt_id`` binds the intent back to its upstream producer receipt.
    """

    record_kind: str
    intent_id: str
    route: str
    receipt_id: str
    attempt_no: int
    lease_until: datetime
    recovered_at: datetime
    reason_code: str
    recovered_by: str

    def __post_init__(self) -> None:
        if self.record_kind != RECORD_KIND or type(self.record_kind) is not str:
            raise LeaseRecoveryError("RECORD_KIND_INVALID")
        _label(self.intent_id, "RECORD_INTENT_ID")
        if type(self.route) is not str or self.route not in DISPATCH_ROUTES:
            raise LeaseRecoveryError("RECORD_ROUTE_INVALID")
        _label(self.receipt_id, "RECORD_RECEIPT_ID")
        if type(self.attempt_no) is not int or self.attempt_no < 1:
            raise LeaseRecoveryError("RECORD_ATTEMPT_NO_INVALID")
        _utc(self.lease_until, "RECORD_LEASE_UNTIL")
        _utc(self.recovered_at, "RECORD_RECOVERED_AT")
        if not (self.lease_until < self.recovered_at):
            raise LeaseRecoveryError("RECORD_TIME_ORDER_INVALID")
        if self.reason_code != REASON_LEASE_EXPIRED or type(self.reason_code) is not str:
            raise LeaseRecoveryError("RECORD_REASON_INVALID")
        _label(self.recovered_by, "RECORD_RECOVERED_BY")

    def payload(self) -> Dict[str, object]:
        """Canonical JSON view; datetimes render as canonical UTC text."""
        return {
            "attempt_no": self.attempt_no,
            "intent_id": self.intent_id,
            "lease_until": utc_text(self.lease_until),
            "reason_code": self.reason_code,
            "receipt_id": self.receipt_id,
            "recovered_at": utc_text(self.recovered_at),
            "recovered_by": self.recovered_by,
            "record_kind": self.record_kind,
            "route": self.route,
        }


def _parse_utc(value: object) -> datetime:
    if type(value) is not str:
        raise LeaseRecoveryError("RECORD_TIME_INVALID")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LeaseRecoveryError("RECORD_TIME_INVALID") from exc
    _utc(parsed, "RECORD_TIME")
    return parsed


def _parse_line(line: bytes) -> LeaseRecoveryRecord:
    """Strictly parse one canonical JSONL line back into a record."""

    def unique(pairs: List[Tuple[str, object]]) -> Dict[str, object]:
        result: Dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise LeaseRecoveryError("RECORD_LINE_DUPLICATE_KEY")
            result[key] = value
        return result

    try:
        value = json.loads(line.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LeaseRecoveryError("RECORD_LINE_INVALID") from exc
    if type(value) is not dict or set(value) != _RECORD_FIELDS:
        raise LeaseRecoveryError("RECORD_FIELDS_INVALID")
    try:
        if canonical_bytes(value) != line:
            raise LeaseRecoveryError("RECORD_LINE_NOT_CANONICAL")
    except ValueError as exc:
        raise LeaseRecoveryError("RECORD_LINE_NOT_CANONICAL") from exc
    try:
        return LeaseRecoveryRecord(
            record_kind=value["record_kind"],  # type: ignore[arg-type]
            intent_id=value["intent_id"],  # type: ignore[arg-type]
            route=value["route"],  # type: ignore[arg-type]
            receipt_id=value["receipt_id"],  # type: ignore[arg-type]
            attempt_no=value["attempt_no"],  # type: ignore[arg-type]
            lease_until=_parse_utc(value["lease_until"]),
            recovered_at=_parse_utc(value["recovered_at"]),
            reason_code=value["reason_code"],  # type: ignore[arg-type]
            recovered_by=value["recovered_by"],  # type: ignore[arg-type]
        )
    except KeyError as exc:
        raise LeaseRecoveryError("RECORD_FIELDS_INVALID") from exc


class LeaseRecoveryLog:
    """Append-only recovery record log with an optional JSONL mirror.

    Without a path the log is memory-only, matching the package's offline
    no-persistence default. With a path every appended record is also
    written as one canonical JSON line in binary append mode; existing
    file bytes are never rewritten, so repeated recovery is byte-idempotent.
    Prior records are loaded strictly on construction: a non-canonical or
    malformed line fails closed instead of being silently skipped.
    """

    def __init__(self, path: Optional[str] = None) -> None:
        if path is not None and (type(path) is not str or not path):
            raise LeaseRecoveryError("LOG_PATH_INVALID")
        self._path = path
        self._records: List[LeaseRecoveryRecord] = []
        if path is not None:
            self._load()

    @property
    def path(self) -> Optional[str]:
        return self._path

    @property
    def records(self) -> Tuple[LeaseRecoveryRecord, ...]:
        return tuple(self._records)

    @property
    def intent_ids(self) -> FrozenSet[str]:
        return frozenset(record.intent_id for record in self._records)

    def has_intent(self, intent_id: object) -> bool:
        if type(intent_id) is not str:
            return False
        return any(record.intent_id == intent_id for record in self._records)

    def append(self, record: LeaseRecoveryRecord) -> LeaseRecoveryRecord:
        """Append one record; a duplicate intent id fails closed."""
        if type(record) is not LeaseRecoveryRecord:
            raise LeaseRecoveryError("RECORD_INVALID")
        if self.has_intent(record.intent_id):
            raise LeaseRecoveryError("RECORD_INTENT_DUPLICATE")
        self._records.append(record)
        if self._path is not None:
            with open(self._path, "ab") as handle:
                handle.write(canonical_bytes(record.payload()) + b"\n")
        return record

    def _load(self) -> None:
        try:
            with open(self._path, "rb") as handle:  # type: ignore[arg-type]
                raw = handle.read()
        except FileNotFoundError:
            return
        for line in raw.splitlines():
            if line.strip():
                self._records.append(_parse_line(line))


@dataclass(frozen=True)
class LeaseRecoveryReport:
    """Outcome of one recovery scan; counts and ids only."""

    scanned: int
    recovered: Tuple[str, ...]
    already_recovered: Tuple[str, ...]


def _suspended_attempt(ledger: DispatchLedger, intent: DispatchIntent,
                       now: datetime) -> Optional[DispatchAttempt]:
    """Current attempt of a lease-expired suspended intent, else None.

    The suspended form is exactly the state ``reclaim_expired`` accepts:
    status CLAIMED, current attempt outcome CLAIMED, and the lease fully
    elapsed (now strictly greater than lease_until). Every other state —
    READY, terminal outcomes, cancelled, revoked, live lease, or the expiry
    boundary itself — is not suspended and is never recovered.
    """
    if ledger.status(intent.intent_id) != CLAIMED:
        return None
    attempts = ledger.get_attempts(intent.intent_id)
    if not attempts:
        return None
    current = attempts[-1]
    if current.outcome != CLAIMED or not (now > current.lease_until):
        return None
    return current


def recover_expired_intents(ledger: DispatchLedger, log: LeaseRecoveryLog, *,
                            now: datetime,
                            recovered_by: str) -> LeaseRecoveryReport:
    """Explicitly recover every lease-expired suspended intent of one ledger.

    Operator entry in the style of the existing ledger APIs (one call per
    route ledger with that ledger's own log). For each suspended intent not
    already present in the log, the existing explicit reclaim
    (``reclaim_expired``) is invoked and one append-only recovery record is
    written. Intents already recovered are skipped without any state change;
    unexpired, completed, cancelled and revoked intents are never touched.

    A failed record append aborts the scan with the error propagated; the
    already-reclaimed intents of that run stay reclaimed and a re-run is
    safe (no duplicate records, nothing double-reclaimed).
    """
    if type(ledger) is not DispatchLedger:
        raise LeaseRecoveryError("LEDGER_INVALID")
    if type(log) is not LeaseRecoveryLog:
        raise LeaseRecoveryError("LOG_INVALID")
    _utc(now, "RECOVER_NOW")
    _label(recovered_by, "RECOVERED_BY")
    recovered: List[str] = []
    already: List[str] = []
    intents = ledger.list_intents()
    for intent in intents:
        attempt = _suspended_attempt(ledger, intent, now)
        if attempt is None:
            continue
        if log.has_intent(intent.intent_id):
            already.append(intent.intent_id)
            continue
        ledger.reclaim_expired(intent.intent_id, now=now)
        log.append(LeaseRecoveryRecord(
            record_kind=RECORD_KIND,
            intent_id=intent.intent_id,
            route=intent.route,
            receipt_id=intent.receipt_id,
            attempt_no=attempt.attempt_no,
            lease_until=attempt.lease_until,
            recovered_at=now,
            reason_code=REASON_LEASE_EXPIRED,
            recovered_by=recovered_by,
        ))
        recovered.append(intent.intent_id)
    return LeaseRecoveryReport(scanned=len(intents),
                               recovered=tuple(recovered),
                               already_recovered=tuple(already))
