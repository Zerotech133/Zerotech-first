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


SCHEMA_VERSION = 2
STATES = frozenset({"READY", "CLAIMED", "SENT", "FAILED", "UNKNOWN", "CANCELLED"})
CST = timezone(timedelta(hours=8))
DAILY_EVENT_CAP = 3
MAX_ATTEMPTS = 3
RETRY_BASE = timedelta(minutes=1)


def _utc(value: datetime) -> str:
    """Normalize an aware instant to a sortable UTC ISO-8601 timestamp."""
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("an explicit timezone-aware timestamp is required")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _evidence_in_window(
    observed_at: datetime, claimed_at: str, finished_at: str, expires_at: str
) -> bool:
    """Require an aware observation during this claim, no later than finish."""
    observed = _utc(observed_at)
    start = _utc(datetime.fromisoformat(claimed_at))
    finish = _utc(datetime.fromisoformat(finished_at))
    end = _utc(datetime.fromisoformat(expires_at))
    return start <= observed <= min(finish, end)


def _text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} must be nonempty, unpadded text")
    return value


def _key(parts: dict[str, str]) -> str:
    payload = json.dumps(
        parts, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def event_key(
    sub_id: str, kind: str, mode: str, fingerprint: str, source_anchor: str
) -> str:
    """Identify immutable event content, including the exact quote/source instant.

    fingerprint must be a canonical *content* digest supplied by the producer;
    source_anchor must identify its authoritative batch/quote instant. Never
    substitute a day, wall-clock send time, or display text for either input.
    """
    return _key(
        {
            "v": "1",
            "sub_id": _text(sub_id, "sub_id"),
            "kind": _text(kind, "kind"),
            "mode": _text(mode, "mode"),
            "fingerprint": _text(fingerprint, "fingerprint"),
            "source_anchor": _text(source_anchor, "source_anchor"),
        }
    )


def target_key(target_type: str, target_id: str) -> str:
    """Identify a recipient using its stable transport type and id, not alias."""
    return _key(
        {
            "v": "1",
            "type": _text(target_type, "target_type"),
            "id": _text(target_id, "target_id"),
        }
    )


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
    trace_id: str


@dataclass(frozen=True)
class NonAcceptanceProof:
    """Trusted provider/hub evidence that this exact attempt was not forwarded."""

    event_key: str
    target_key: str
    attempt_id: str
    route: str
    trace_id: str
    evidence_type: str
    reason: str
    observed_at: datetime


class DeliveryLedger:
    """SQLite journal with one connection and atomic transaction per operation.

    ``receipt_verifier`` and ``non_acceptance_verifier`` are externally injected
    trust boundaries. The first must verify actual provider acceptance and the
    second must verify affirmative non-forwarding/non-acceptance evidence.
    Neither callback may infer its answer from boolean send return, a string
    prefix, local trace id, or HTTP status. Missing or throwing verifiers fail
    closed to UNKNOWN. This class never sends or fetches evidence; a callback
    returning True for fabricated evidence is NOT independent verification.
    This isolated ledger is not production-ready: externally authenticated
    authorization and underlying provider message_id must be supplied upstream.
    Ambiguous network failures cannot provide exactly-once provider delivery.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        receipt_verifier: Optional[Callable[[ProviderReceipt], bool]] = None,
        non_acceptance_verifier: Optional[Callable[[NonAcceptanceProof], bool]] = None,
    ) -> None:
        self.path = str(path)
        if self.path == ":memory:":
            raise ValueError("use an on-disk SQLite path for restart safety")
        self.clock = clock
        self.receipt_verifier = receipt_verifier
        self.non_acceptance_verifier = non_acceptance_verifier
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, SCHEMA_VERSION):
                raise RuntimeError(
                    f"unsupported delivery ledger schema version {version}"
                )
            if version == 0:
                # No existing tables need preservation for a new database.
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
                db.execute("BEGIN IMMEDIATE")
            if version < SCHEMA_VERSION:
                # execute(), unlike executescript(), does not silently commit the
                # surrounding migration before all changes succeed.
                for statement in (
                    """CREATE TABLE subscription_control (
                        sub_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL CHECK (enabled IN (0,1)),
                        epoch INTEGER NOT NULL CHECK (epoch > 0), updated_at TEXT NOT NULL)""",
                    "ALTER TABLE delivery_events ADD COLUMN source_at TEXT",
                    "ALTER TABLE delivery_events ADD COLUMN source_day_cst TEXT",
                    "ALTER TABLE delivery_events ADD COLUMN payload TEXT",
                    "ALTER TABLE delivery_events ADD COLUMN payload_hash TEXT",
                    "ALTER TABLE delivery_events ADD COLUMN valid_until TEXT",
                    "ALTER TABLE delivery_events ADD COLUMN control_epoch INTEGER",
                    "ALTER TABLE delivery_targets ADD COLUMN retry_after TEXT",
                    "CREATE INDEX delivery_events_day_idx ON delivery_events(sub_id,source_day_cst)",
                ):
                    db.execute(statement)
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

    def set_subscription_control(
        self, sub_id: str, *, enabled: bool, epoch: int
    ) -> None:
        """Persist an externally authorized control decision; epochs only advance.

        The caller MUST authenticate subscription ownership independently. Revoking
        a claim records UNKNOWN, since an already forwarded message is not recalled.
        """
        _text(sub_id, "sub_id")
        if type(enabled) is not bool or type(epoch) is not int or epoch <= 0:
            raise ValueError(
                "enabled must be boolean and epoch must be positive integer"
            )
        now = self._now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT enabled,epoch FROM subscription_control WHERE sub_id=?",
                (sub_id,),
            ).fetchone()
            if old is not None:
                if epoch < old["epoch"] or (
                    epoch == old["epoch"] and enabled != bool(old["enabled"])
                ):
                    raise ValueError("stale or conflicting subscription control")
                if epoch == old["epoch"]:
                    return
            db.execute(
                """INSERT INTO subscription_control(sub_id,enabled,epoch,updated_at)
                VALUES (?,?,?,?) ON CONFLICT(sub_id) DO UPDATE SET
                enabled=excluded.enabled,epoch=excluded.epoch,updated_at=excluded.updated_at""",
                (sub_id, int(enabled), epoch, now),
            )
            if old is not None:
                # Epoch rotation invalidates every unsent target, even on re-enable.
                attempts = db.execute(
                    """SELECT t.attempt_id FROM delivery_targets t
                    JOIN delivery_events e ON e.event_key=t.event_key
                    WHERE e.sub_id=? AND t.state='CLAIMED'""",
                    (sub_id,),
                ).fetchall()
                for attempt in attempts:
                    db.execute(
                        """UPDATE delivery_attempts SET outcome='UNKNOWN',completed_at=?
                        WHERE attempt_id=? AND outcome='CLAIMED'""",
                        (now, attempt["attempt_id"]),
                    )
                db.execute(
                    """UPDATE delivery_targets SET state='UNKNOWN',updated_at=?
                    WHERE state='CLAIMED' AND event_key IN
                    (SELECT event_key FROM delivery_events WHERE sub_id=?)""",
                    (now, sub_id),
                )
                db.execute(
                    """UPDATE delivery_targets SET state='CANCELLED',updated_at=?
                    WHERE state IN ('READY','FAILED') AND event_key IN
                    (SELECT event_key FROM delivery_events WHERE sub_id=?)""",
                    (now, sub_id),
                )
            db.commit()

    def register(
        self,
        sub_id: str,
        kind: str,
        mode: str,
        fingerprint: str,
        source_anchor: str,
        target_type: str,
        target_id: str,
        *,
        source_at: Optional[datetime] = None,
        payload: Optional[str] = None,
        valid_until: Optional[datetime] = None,
        expected_epoch: Optional[int] = None,
    ) -> tuple[str, str]:
        """Register frozen content under the current authorization epoch.

        Legacy callers lacking explicit source, content and epoch fail closed. The
        source instant, rather than send time, defines the Beijing business day.
        """
        event = event_key(sub_id, kind, mode, fingerprint, source_anchor)
        target = target_key(target_type, target_id)
        if (
            source_at is None
            or valid_until is None
            or payload is None
            or type(expected_epoch) is not int
            or expected_epoch <= 0
        ):
            raise ValueError(
                "source_at, payload, valid_until and expected_epoch are required"
            )
        source_utc = _utc(source_at)
        until = _utc(valid_until)
        now = self._now()
        if source_utc > now or until <= now or until <= source_utc:
            raise ValueError("future source or expired event")
        if not isinstance(payload, str) or not payload:
            raise ValueError("payload must be nonempty text")
        day = source_at.astimezone(CST).date().isoformat()
        payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            control = db.execute(
                "SELECT enabled,epoch FROM subscription_control WHERE sub_id=?",
                (sub_id,),
            ).fetchone()
            if (
                control is None
                or not control["enabled"]
                or control["epoch"] != expected_epoch
            ):
                raise ValueError("subscription authorization missing, revoked or stale")
            # A pre-v2 event has no trustworthy source day or control epoch. Do
            # not let fresh events consume slots while old deliveries are unknown.
            if (
                db.execute(
                    """SELECT 1 FROM delivery_events WHERE sub_id=?
                    AND (source_day_cst IS NULL OR control_epoch IS NULL) LIMIT 1""",
                    (sub_id,),
                ).fetchone()
                is not None
            ):
                raise ValueError("legacy deliveries require offline reconciliation")
            row = db.execute(
                "SELECT * FROM delivery_events WHERE event_key=?", (event,)
            ).fetchone()
            if row is not None:
                if (
                    row["cancelled_at"] is not None
                    or row["control_epoch"] != expected_epoch
                    or row["source_at"] != source_utc
                    or row["source_day_cst"] != day
                    or row["payload_hash"] != payload_hash
                    or row["payload"] != payload
                    or row["valid_until"] != until
                ):
                    raise ValueError("event identity conflict or cancelled event")
            else:
                db.execute(
                    """INSERT INTO delivery_events
                    (event_key,sub_id,kind,mode,fingerprint,source_anchor,created_at,
                     source_at,source_day_cst,payload,payload_hash,valid_until,control_epoch)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        event,
                        sub_id,
                        kind,
                        mode,
                        fingerprint,
                        source_anchor,
                        now,
                        source_utc,
                        day,
                        payload,
                        payload_hash,
                        until,
                        expected_epoch,
                    ),
                )
            db.execute(
                """INSERT OR IGNORE INTO delivery_targets
                (event_key,target_key,target_type,target_id,state,updated_at)
                VALUES (?,?,?,?,'READY',?)""",
                (event, target, target_type, target_id, now),
            )
            db.commit()
        return event, target

    def get(self, event: str, target: str) -> Optional[dict[str, object]]:
        """Return a snapshot of the target and its current attempt, if present."""
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM delivery_targets WHERE event_key=? AND target_key=?",
                (event, target),
            ).fetchone()
            return dict(row) if row is not None else None

    def claim(
        self,
        event: str,
        target: str,
        route: str,
        *,
        lease: timedelta = timedelta(minutes=5),
        retry_failed: bool = False,
        expected_epoch: Optional[int] = None,
    ) -> Optional[Claim]:
        """Atomically claim READY, or FAILED only under an explicit retry policy.

        UNKNOWN is NEVER claimable, even after its lease expires. There is no
        automatic assumption that a timed-out send failed at the provider.
        """
        _text(route, "route")
        if type(expected_epoch) is not int or expected_epoch <= 0:
            raise ValueError("claim requires a positive expected_epoch")
        if not isinstance(lease, timedelta) or lease <= timedelta(0):
            raise ValueError("lease must be a positive timedelta")
        claimed_at = self._now()
        expires_at = _utc(datetime.fromisoformat(claimed_at) + lease)
        attempt = uuid.uuid4().hex
        trace = uuid.uuid4().hex
        states = ("READY", "FAILED") if retry_failed else ("READY",)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            event_row = db.execute(
                """SELECT e.cancelled_at,e.sub_id,e.source_day_cst,
                e.valid_until,e.control_epoch,c.enabled,c.epoch
                FROM delivery_events e LEFT JOIN subscription_control c ON c.sub_id=e.sub_id
                WHERE e.event_key=?""",
                (event,),
            ).fetchone()
            if (
                event_row is None
                or event_row["cancelled_at"] is not None
                or event_row["control_epoch"] != expected_epoch
                or event_row["enabled"] != 1
                or event_row["epoch"] != expected_epoch
                or event_row["valid_until"] is None
                or claimed_at >= event_row["valid_until"]
                or expires_at > event_row["valid_until"]
            ):
                return None
            target_row = db.execute(
                """SELECT state,retry_after FROM delivery_targets
                WHERE event_key=? AND target_key=?""",
                (event, target),
            ).fetchone()
            if target_row is None or target_row["state"] not in states:
                return None
            count = db.execute(
                """SELECT count(*) FROM delivery_attempts
                WHERE event_key=? AND target_key=?""",
                (event, target),
            ).fetchone()[0]
            if count >= MAX_ATTEMPTS:
                return None
            if target_row["state"] == "FAILED" and (
                target_row["retry_after"] is None
                or claimed_at < target_row["retry_after"]
            ):
                return None
            occupied = db.execute(
                """SELECT count(DISTINCT e.event_key)
                FROM delivery_events e JOIN delivery_targets t ON t.event_key=e.event_key
                WHERE e.sub_id=? AND e.source_day_cst=? AND e.event_key<>?
                    AND t.state IN ('CLAIMED','UNKNOWN','SENT')""",
                (event_row["sub_id"], event_row["source_day_cst"], event),
            ).fetchone()[0]
            self_occupied = (
                db.execute(
                    """SELECT 1 FROM delivery_targets
                WHERE event_key=? AND state IN ('CLAIMED','UNKNOWN','SENT') LIMIT 1""",
                    (event,),
                ).fetchone()
                is not None
            )
            if occupied >= DAILY_EVENT_CAP and not self_occupied:
                return None
            markers = ",".join("?" for _ in states)
            changed = db.execute(
                """UPDATE delivery_targets
                SET state='CLAIMED', attempt_id=?, route=?, receipt_id=NULL,
                    trace_id=?, claimed_at=?, expires_at=?, updated_at=?
                WHERE event_key=? AND target_key=? AND state IN (__MARKERS__)""".replace("__MARKERS__", markers),
                (
                    attempt,
                    route,
                    trace,
                    claimed_at,
                    expires_at,
                    claimed_at,
                    event,
                    target,
                    *states,
                ),
            )
            if changed.rowcount != 1:
                return None
            db.execute(
                """INSERT INTO delivery_attempts
                (attempt_id,event_key,target_key,route,claimed_at,expires_at,outcome,trace_id)
                VALUES (?,?,?,?,?,?,'CLAIMED',?)""",
                (attempt, event, target, route, claimed_at, expires_at, trace),
            )
            db.commit()
        return Claim(event, target, attempt, route, expires_at, trace)

    def finish(
        self,
        claim: Claim,
        *,
        outcome: str,
        receipt: Optional[ProviderReceipt] = None,
        non_acceptance: Optional[NonAcceptanceProof] = None,
    ) -> bool:
        """Finalize one attempt, conservatively treating unverified evidence as UNKNOWN.

        FAILED requires a trusted proof of definite non-acceptance and cannot be
        based on an exception or timeout. SENT requires independently verified
        provider acceptance. The return value denotes a state change, not delivery.
        """
        if not isinstance(claim, Claim):
            raise TypeError("claim must be a Claim")
        if outcome not in ("SENT", "FAILED", "UNKNOWN"):
            raise ValueError("unsupported delivery outcome")
        now = self._now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT state,claimed_at,expires_at,trace_id,route FROM delivery_targets
                WHERE event_key=? AND target_key=? AND attempt_id=?""",
                (claim.event_key, claim.target_key, claim.attempt_id),
            ).fetchone()
            if (
                row is None
                or row["state"] != "CLAIMED"
                or row["route"] != claim.route
                or row["trace_id"] != claim.trace_id
            ):
                return False
            identity = (
                claim.event_key,
                claim.target_key,
                claim.attempt_id,
                claim.route,
                claim.trace_id,
            )
            actual = "UNKNOWN"
            if (
                now < row["expires_at"]
                and outcome == "SENT"
                and isinstance(receipt, ProviderReceipt)
            ):
                evidence = (
                    receipt.event_key,
                    receipt.target_key,
                    receipt.attempt_id,
                    receipt.route,
                    receipt.trace_id,
                )
                if (
                    evidence == identity
                    and isinstance(receipt.receipt_id, str)
                    and receipt.receipt_id.strip()
                ):
                    try:
                        actual = (
                            "SENT"
                            if (
                                self.receipt_verifier is not None
                                and _evidence_in_window(
                                    receipt.observed_at,
                                    row["claimed_at"],
                                    now,
                                    row["expires_at"],
                                )
                                and self.receipt_verifier(receipt) is True
                            )
                            else "UNKNOWN"
                        )
                    except Exception:
                        actual = "UNKNOWN"
            elif (
                now < row["expires_at"]
                and outcome == "FAILED"
                and isinstance(non_acceptance, NonAcceptanceProof)
            ):
                evidence = (
                    non_acceptance.event_key,
                    non_acceptance.target_key,
                    non_acceptance.attempt_id,
                    non_acceptance.route,
                    non_acceptance.trace_id,
                )
                if (
                    evidence == identity
                    and non_acceptance.evidence_type
                    in ("provider_rejected", "hub_not_forwarded")
                    and isinstance(non_acceptance.reason, str)
                    and non_acceptance.reason.strip()
                ):
                    try:
                        actual = (
                            "FAILED"
                            if (
                                self.non_acceptance_verifier is not None
                                and _evidence_in_window(
                                    non_acceptance.observed_at,
                                    row["claimed_at"],
                                    now,
                                    row["expires_at"],
                                )
                                and self.non_acceptance_verifier(non_acceptance) is True
                            )
                            else "UNKNOWN"
                        )
                    except Exception:
                        actual = "UNKNOWN"
            receipt_id = (
                receipt.receipt_id
                if actual == "SENT" and isinstance(receipt, ProviderReceipt)
                else None
            )
            retry_after = None
            if actual == "FAILED":
                count = db.execute(
                    """SELECT count(*) FROM delivery_attempts
                    WHERE event_key=? AND target_key=?""",
                    (claim.event_key, claim.target_key),
                ).fetchone()[0]
                retry_after = _utc(
                    datetime.fromisoformat(now) + RETRY_BASE * (2 ** (count - 1))
                )
            changed = db.execute(
                """UPDATE delivery_targets
                SET state=?, receipt_id=?, retry_after=?, updated_at=?
                WHERE event_key=? AND target_key=? AND attempt_id=? AND state='CLAIMED'""",
                (
                    actual,
                    receipt_id,
                    retry_after,
                    now,
                    claim.event_key,
                    claim.target_key,
                    claim.attempt_id,
                ),
            )
            if changed.rowcount != 1:
                return False
            db.execute(
                """UPDATE delivery_attempts SET outcome=?, completed_at=?, receipt_id=?
                WHERE attempt_id=? AND trace_id=? AND outcome='CLAIMED'""",
                (actual, now, receipt_id, claim.attempt_id, claim.trace_id),
            )
            db.commit()
            return True

    def expire(self) -> int:
        """Move expired in-flight claims to UNKNOWN, including after restart."""
        now = self._now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                """SELECT attempt_id FROM delivery_targets
                WHERE state='CLAIMED' AND expires_at<=?""",
                (now,),
            ).fetchall()
            for row in rows:
                db.execute(
                    """UPDATE delivery_attempts
                    SET outcome='UNKNOWN', completed_at=?
                    WHERE attempt_id=? AND outcome='CLAIMED'""",
                    (now, row["attempt_id"]),
                )
            count = db.execute(
                """UPDATE delivery_targets SET state='UNKNOWN', updated_at=?
                WHERE state='CLAIMED' AND expires_at<=?""",
                (now, now),
            ).rowcount
            db.commit()
            return count

    def cancel(self, event: str) -> bool:
        """Revoke an event; in-flight attempts remain UNKNOWN for reconciliation."""
        now = self._now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                """UPDATE delivery_events SET cancelled_at=?
                WHERE event_key=? AND cancelled_at IS NULL""",
                (now, event),
            )
            if changed.rowcount != 1:
                return False
            attempts = db.execute(
                """SELECT attempt_id FROM delivery_targets
                WHERE event_key=? AND state='CLAIMED'""",
                (event,),
            ).fetchall()
            for row in attempts:
                db.execute(
                    """UPDATE delivery_attempts SET outcome='UNKNOWN', completed_at=?
                    WHERE attempt_id=? AND outcome='CLAIMED'""",
                    (now, row["attempt_id"]),
                )
            db.execute(
                """UPDATE delivery_targets SET state='UNKNOWN', updated_at=?
                WHERE event_key=? AND state='CLAIMED'""",
                (now, event),
            )
            db.execute(
                """UPDATE delivery_targets SET state='CANCELLED', updated_at=?
                WHERE event_key=? AND state IN ('READY','FAILED')""",
                (now, event),
            )
            db.commit()
            return True

    def attempts(self, event: str, target: str) -> list[dict[str, object]]:
        """Return durable attempt history for audit and manual reconciliation."""
        with self._connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    """SELECT * FROM delivery_attempts
                WHERE event_key=? AND target_key=? ORDER BY rowid""",
                    (event, target),
                )
            ]
