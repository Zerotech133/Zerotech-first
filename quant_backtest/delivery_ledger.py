"""Offline, per-recipient delivery ledger; no network or sender is imported.

A CLAIMED delivery may have reached the provider before a process crashes. Expiry
therefore produces UNKNOWN, never a retryable failure. SENT requires a provider
receipt bound to the exact event, target, and attempt, plus a caller-supplied
independent verifier. A verifier must check provider evidence, not return True
merely because a send call returned successfully.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional


SCHEMA_VERSION = 1
STATES = frozenset({"READY", "CLAIMED", "SENT", "FAILED", "UNKNOWN", "CANCELLED"})


def _utc(value: datetime) -> str:
    """Normalize an aware instant to a sortable UTC ISO-8601 timestamp."""
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("an explicit timezone-aware timestamp is required")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} must be nonempty, unpadded text")
    return value


def _key(parts: dict[str, str]) -> str:
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def event_key(sub_id: str, kind: str, mode: str, fingerprint: str,
              source_anchor: str) -> str:
    """Identify immutable event content, including the exact quote/source instant.

    fingerprint must be a canonical *content* digest supplied by the producer;
    source_anchor must identify its authoritative batch/quote instant. Never
    substitute a day, wall-clock send time, or display text for either input.
    """
    return _key({"v": "1", "sub_id": _text(sub_id, "sub_id"),
                 "kind": _text(kind, "kind"), "mode": _text(mode, "mode"),
                 "fingerprint": _text(fingerprint, "fingerprint"),
                 "source_anchor": _text(source_anchor, "source_anchor")})


def target_key(target_type: str, target_id: str) -> str:
    """Identify a recipient using its stable transport type and id, not alias."""
    return _key({"v": "1", "type": _text(target_type, "target_type"),
                 "id": _text(target_id, "target_id")})


@dataclass(frozen=True)
class ProviderReceipt:
    """Evidence to verify independently against a real provider before SENT."""

    event_key: str
    target_key: str
    attempt_id: str
    route: str
    receipt_id: str
    trace_id: str
    observed_at: datetime


@dataclass(frozen=True)
class Claim:
    """An exclusive claim; the sender must persist the result of this attempt."""

    event_key: str
    target_key: str
    attempt_id: str
    route: str
    expires_at: str


class DeliveryLedger:
    """SQLite journal with one connection and atomic transaction per operation.

    ``receipt_verifier`` is an external trust boundary. It must verify a receipt
    with the actual provider; leaving it unset fails closed for SENT. This class
    never sends or fetches receipts and cannot promise exactly-once provider
    delivery after an ambiguous network failure.
    """

    def __init__(self, path: str | Path, *,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 receipt_verifier: Optional[Callable[[ProviderReceipt], bool]] = None) -> None:
        self.path = str(path)
        if self.path == ":memory:":
            raise ValueError("use an on-disk SQLite path for restart safety")
        self.clock = clock
        self.receipt_verifier = receipt_verifier
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise RuntimeError(f"unsupported delivery ledger schema version {version}")
            if version == 0:
                db.executescript("""
                    CREATE TABLE delivery_events (
                        event_key TEXT PRIMARY KEY, sub_id TEXT NOT NULL,
                        kind TEXT NOT NULL, mode TEXT NOT NULL,
                        fingerprint TEXT NOT NULL, source_anchor TEXT NOT NULL,
                        created_at TEXT NOT NULL, cancelled_at TEXT
                    );
                    CREATE TABLE delivery_targets (
                        event_key TEXT NOT NULL REFERENCES delivery_events(event_key),
                        target_key TEXT NOT NULL, target_type TEXT NOT NULL,
                        target_id TEXT NOT NULL, state TEXT NOT NULL
                            CHECK (state IN ('READY','CLAIMED','SENT','FAILED','UNKNOWN','CANCELLED')),
                        attempt_id TEXT, route TEXT, receipt_id TEXT, trace_id TEXT,
                        claimed_at TEXT, expires_at TEXT, updated_at TEXT NOT NULL,
                        PRIMARY KEY (event_key, target_key)
                    );
                    CREATE TABLE delivery_attempts (
                        attempt_id TEXT PRIMARY KEY, event_key TEXT NOT NULL,
                        target_key TEXT NOT NULL, route TEXT NOT NULL,
                        claimed_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                        outcome TEXT NOT NULL CHECK (outcome IN ('CLAIMED','SENT','FAILED','UNKNOWN')),
                        completed_at TEXT, receipt_id TEXT, trace_id TEXT,
                        FOREIGN KEY (event_key,target_key)
                            REFERENCES delivery_targets(event_key,target_key)
                    );
                    CREATE INDEX delivery_attempts_target_idx
                        ON delivery_attempts(event_key,target_key);
                """)
                db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            db.commit()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Close connections on all paths (SQLite context alone does not close)."""
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout = 10000")
            db.execute("PRAGMA foreign_keys = ON")
            yield db
            if db.in_transaction:
                db.commit()
        except BaseException:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.close()

    def _now(self) -> str:
        return _utc(self.clock())

    def register(self, sub_id: str, kind: str, mode: str, fingerprint: str,
                 source_anchor: str, target_type: str, target_id: str) -> tuple[str, str]:
        """Idempotently register exactly one event/target; reject cancelled events."""
        event = event_key(sub_id, kind, mode, fingerprint, source_anchor)
        target = target_key(target_type, target_id)
        now = self._now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""INSERT OR IGNORE INTO delivery_events
                (event_key,sub_id,kind,mode,fingerprint,source_anchor,created_at)
                VALUES (?,?,?,?,?,?,?)""",
                (event, sub_id, kind, mode, fingerprint, source_anchor, now))
            row = db.execute("SELECT cancelled_at FROM delivery_events WHERE event_key=?",
                             (event,)).fetchone()
            if row["cancelled_at"] is not None:
                raise ValueError("event has been cancelled")
            db.execute("""INSERT OR IGNORE INTO delivery_targets
                (event_key,target_key,target_type,target_id,state,updated_at)
                VALUES (?,?,?,?,'READY',?)""",
                (event, target, target_type, target_id, now))
            db.commit()
        return event, target

    def get(self, event: str, target: str) -> Optional[dict[str, object]]:
        """Return a snapshot of the target and its current attempt, if present."""
        with self._connect() as db:
            row = db.execute("SELECT * FROM delivery_targets WHERE event_key=? AND target_key=?",
                             (event, target)).fetchone()
            return dict(row) if row is not None else None

    def claim(self, event: str, target: str, route: str, *,
              lease: timedelta = timedelta(minutes=5),
              retry_failed: bool = False) -> Optional[Claim]:
        """Atomically claim READY, or FAILED only under an explicit retry policy.

        UNKNOWN is NEVER claimable, even after its lease expires. There is no
        automatic assumption that a timed-out send failed at the provider.
        """
        _text(route, "route")
        if not isinstance(lease, timedelta) or lease <= timedelta(0):
            raise ValueError("lease must be a positive timedelta")
        claimed_at = self._now()
        expires_at = _utc(datetime.fromisoformat(claimed_at) + lease)
        attempt = uuid.uuid4().hex
        states = ("READY", "FAILED") if retry_failed else ("READY",)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            event_row = db.execute("SELECT cancelled_at FROM delivery_events WHERE event_key=?",
                                   (event,)).fetchone()
            if event_row is None or event_row["cancelled_at"] is not None:
                return None
            markers = ",".join("?" for _ in states)
            changed = db.execute(f"""UPDATE delivery_targets
                SET state='CLAIMED', attempt_id=?, route=?, receipt_id=NULL,
                    trace_id=NULL, claimed_at=?, expires_at=?, updated_at=?
                WHERE event_key=? AND target_key=? AND state IN ({markers})""",
                (attempt, route, claimed_at, expires_at, claimed_at, event, target, *states))
            if changed.rowcount != 1:
                return None
            db.execute("""INSERT INTO delivery_attempts
                (attempt_id,event_key,target_key,route,claimed_at,expires_at,outcome)
                VALUES (?,?,?,?,?,?,'CLAIMED')""",
                (attempt, event, target, route, claimed_at, expires_at))
            db.commit()
        return Claim(event, target, attempt, route, expires_at)

    def finish(self, claim: Claim, *, outcome: str,
               receipt: Optional[ProviderReceipt] = None,
               trace_id: Optional[str] = None) -> bool:
        """Finalize a claim with FAILED/UNKNOWN or verified SENT.

        If a claimed send reports success without independently verified provider
        evidence, it is UNKNOWN. FAILED is only for a *definitive* non-delivery;
        exception, timeout, or uncertain return must use UNKNOWN. The return
        value means that this call changed state, NOT that delivery succeeded.
        """
        if not isinstance(claim, Claim):
            raise TypeError("claim must be a Claim")
        if outcome not in ("SENT", "FAILED", "UNKNOWN"):
            raise ValueError("unsupported delivery outcome")
        if trace_id is not None:
            _text(trace_id, "trace_id")
        valid_receipt = False
        if outcome == "SENT" and isinstance(receipt, ProviderReceipt):
            matches = (receipt.event_key == claim.event_key
                       and receipt.target_key == claim.target_key
                       and receipt.attempt_id == claim.attempt_id
                       and receipt.route == claim.route
                       and isinstance(receipt.receipt_id, str)
                       and bool(receipt.receipt_id.strip())
                       and isinstance(receipt.trace_id, str)
                       and bool(receipt.trace_id.strip()))
            if matches:
                _utc(receipt.observed_at)
                valid_receipt = (self.receipt_verifier is not None
                                 and self.receipt_verifier(receipt) is True)
        actual = "SENT" if valid_receipt else ("UNKNOWN" if outcome == "SENT" else outcome)
        now = self._now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT state,expires_at FROM delivery_targets
                WHERE event_key=? AND target_key=? AND attempt_id=?""",
                (claim.event_key, claim.target_key, claim.attempt_id)).fetchone()
            if row is None or row["state"] != "CLAIMED":
                return False
            if now >= row["expires_at"]:
                actual = "UNKNOWN"
            receipt_id = receipt.receipt_id if actual == "SENT" and receipt is not None else None
            final_trace = receipt.trace_id if actual == "SENT" and receipt is not None else trace_id
            changed = db.execute("""UPDATE delivery_targets
                SET state=?, receipt_id=?, trace_id=?, updated_at=?
                WHERE event_key=? AND target_key=? AND attempt_id=? AND state='CLAIMED'""",
                (actual, receipt_id, final_trace, now,
                 claim.event_key, claim.target_key, claim.attempt_id))
            if changed.rowcount != 1:
                return False
            db.execute("""UPDATE delivery_attempts SET outcome=?, completed_at=?,
                receipt_id=?, trace_id=? WHERE attempt_id=? AND outcome='CLAIMED'""",
                (actual, now, receipt_id, final_trace, claim.attempt_id))
            db.commit()
            return True

    def expire(self) -> int:
        """Move expired in-flight claims to UNKNOWN, including after restart."""
        now = self._now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("""SELECT attempt_id FROM delivery_targets
                WHERE state='CLAIMED' AND expires_at<=?""", (now,)).fetchall()
            for row in rows:
                db.execute("""UPDATE delivery_attempts
                    SET outcome='UNKNOWN', completed_at=?
                    WHERE attempt_id=? AND outcome='CLAIMED'""", (now, row["attempt_id"]))
            count = db.execute("""UPDATE delivery_targets SET state='UNKNOWN', updated_at=?
                WHERE state='CLAIMED' AND expires_at<=?""", (now, now)).rowcount
            db.commit()
            return count

    def cancel(self, event: str) -> bool:
        """Revoke an event; in-flight attempts remain UNKNOWN for reconciliation."""
        now = self._now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("""UPDATE delivery_events SET cancelled_at=?
                WHERE event_key=? AND cancelled_at IS NULL""", (now, event))
            if changed.rowcount != 1:
                return False
            attempts = db.execute("""SELECT attempt_id FROM delivery_targets
                WHERE event_key=? AND state='CLAIMED'""", (event,)).fetchall()
            for row in attempts:
                db.execute("""UPDATE delivery_attempts SET outcome='UNKNOWN', completed_at=?
                    WHERE attempt_id=? AND outcome='CLAIMED'""", (now, row["attempt_id"]))
            db.execute("""UPDATE delivery_targets SET state='UNKNOWN', updated_at=?
                WHERE event_key=? AND state='CLAIMED'""", (now, event))
            db.execute("""UPDATE delivery_targets SET state='CANCELLED', updated_at=?
                WHERE event_key=? AND state IN ('READY','FAILED')""", (now, event))
            db.commit()
            return True

    def attempts(self, event: str, target: str) -> list[dict[str, object]]:
        """Return durable attempt history for audit and manual reconciliation."""
        with self._connect() as db:
            return [dict(row) for row in db.execute("""SELECT * FROM delivery_attempts
                WHERE event_key=? AND target_key=? ORDER BY rowid""",
                (event, target))]
