"""Three-route offline dispatch ledgers for accepted upstream reports.

W-Q1 approved scope. Exactly one independent DispatchLedger instance per
route (FEISHU_INFO / RESEARCH_REVIEW / EXCHANGE_OBSERVATION); ledgers never
reference each other's intents or attempts, so an UNKNOWN terminal outcome
on one route can never block the others.

Semantics mirror the offline outbox pattern (copied, not imported - this
package never imports watch_dispatch):
- Dispatch is gated on an injected inbox query proving the upstream report
  is accepted and not revoked (UPSTREAM_NOT_ACCEPTED otherwise).
- plan() is idempotent for identical intents; conflicts fail closed.
- claim() takes a lease-bounded attempt; attempt numbers are capped at 3
  (ATTEMPTS_EXHAUSTED).
- finish(): ACCEPTED_VERIFIED / DEFINITELY_REJECTED require independently
  consistent receipt evidence (external_message_id plus observed_at inside
  the claim window and lease); any evidence mismatch is forced to UNKNOWN.
  UNKNOWN is terminal and never retried automatically; only an
  independently evidenced DEFINITELY_REJECTED returns the intent to READY
  for a bounded controlled retry (still under the 3-attempt ceiling).
- cancel()/revoke(): the control epoch only moves forward; rollback is
  rejected fail-closed.

EXCHANGE_OBSERVATION only routes observation candidates to the trader for
review. Offline only: no network, no persistence, no execution semantics;
no object here carries any trading permission concept.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

from quant_backtest.quant_sector.upstream_inbox import (
    UpstreamReceipt,
    digest,
)

_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_ROUTES = frozenset(("FEISHU_INFO", "RESEARCH_REVIEW", "EXCHANGE_OBSERVATION"))
_MAX_ATTEMPTS = 3

READY = "READY"
CLAIMED = "CLAIMED"
ACCEPTED_VERIFIED = "ACCEPTED_VERIFIED"
DEFINITELY_REJECTED = "DEFINITELY_REJECTED"
UNKNOWN = "UNKNOWN"
CANCELLED = "CANCELLED"
_FINAL_OUTCOMES = frozenset(
    (ACCEPTED_VERIFIED, DEFINITELY_REJECTED, UNKNOWN, CANCELLED))


class DispatchLedgerError(ValueError):
    """Fail-closed dispatch violation."""


def _label(value: object, field: str) -> None:
    if type(value) is not str or _LABEL.fullmatch(value) is None:
        raise DispatchLedgerError(field + "_INVALID")


def _utc(value: object, field: str) -> None:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise DispatchLedgerError(field + "_INVALID")


@dataclass(frozen=True)
class DispatchIntent:
    """One planned delivery of an accepted upstream report on one route."""

    intent_id: str
    receipt_id: str
    route: str
    target_ref: str
    created_at: datetime

    def __post_init__(self) -> None:
        _label(self.intent_id, "INTENT_ID")
        _label(self.receipt_id, "RECEIPT_ID")
        if type(self.route) is not str or self.route not in _ROUTES:
            raise DispatchLedgerError("ROUTE_INVALID")
        _label(self.target_ref, "TARGET_REF")
        _utc(self.created_at, "INTENT_CREATED_AT")


@dataclass(frozen=True)
class DispatchAttempt:
    """One lease-bounded delivery attempt; outcome becomes final via finish."""

    intent_id: str
    attempt_no: int
    claimed_at: datetime
    lease_until: datetime
    outcome: str
    external_message_id: Optional[str] = None
    observed_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        _label(self.intent_id, "INTENT_ID")
        if type(self.attempt_no) is not int or not (1 <= self.attempt_no <= _MAX_ATTEMPTS):
            raise DispatchLedgerError("ATTEMPT_NO_INVALID")
        _utc(self.claimed_at, "CLAIMED_AT")
        _utc(self.lease_until, "LEASE_UNTIL")
        if not (self.claimed_at < self.lease_until):
            raise DispatchLedgerError("LEASE_WINDOW_INVALID")
        if type(self.outcome) is not str or not self.outcome:
            raise DispatchLedgerError("OUTCOME_INVALID")
        if self.external_message_id is not None and (
                type(self.external_message_id) is not str
                or not self.external_message_id
                or len(self.external_message_id) > 128):
            raise DispatchLedgerError("EXTERNAL_MESSAGE_ID_INVALID")
        if self.observed_at is not None:
            _utc(self.observed_at, "OBSERVED_AT")


class DispatchLedger:
    """Per-route offline dispatch ledger; plan idempotent, epochs forward-only."""

    def __init__(self, route: str, *,
                 inbox_lookup: Optional[Callable[[str], bool]] = None) -> None:
        """Bind one route; inbox_lookup is the injected acceptance query."""
        if type(route) is not str or route not in _ROUTES:
            raise DispatchLedgerError("ROUTE_INVALID")
        self._route = route
        self._inbox_lookup = inbox_lookup
        self._intents: Dict[str, DispatchIntent] = {}
        self._statuses: Dict[str, str] = {}
        self._current: Dict[str, DispatchAttempt] = {}
        self._history: Dict[str, List[DispatchAttempt]] = {}
        self._attempt_nos: Dict[str, int] = {}
        self._revoked: set = set()
        self._epoch: int = 0

    @property
    def route(self) -> str:
        return self._route

    @property
    def ledger_epoch(self) -> int:
        return self._epoch

    def plan(self, intent: DispatchIntent, *,
             receipt_view: UpstreamReceipt,
             now: datetime) -> DispatchIntent:
        """Plan a dispatch; requires an active accepted upstream receipt.

        Fail-closed unless the injected inbox query confirms receipt_id is
        accepted and not revoked. Identical re-plan is idempotent.
        """
        _utc(now, "PLAN_NOW")
        if type(intent) is not DispatchIntent:
            raise DispatchLedgerError("INTENT_INVALID")
        if intent.route != self._route:
            raise DispatchLedgerError("ROUTE_MISMATCH")
        if (type(receipt_view) is not UpstreamReceipt
                or receipt_view.receipt_id != intent.receipt_id):
            raise DispatchLedgerError("UPSTREAM_NOT_ACCEPTED")
        if self._inbox_lookup is None or self._inbox_lookup(intent.receipt_id) is not True:
            raise DispatchLedgerError("UPSTREAM_NOT_ACCEPTED")
        if intent.intent_id in self._revoked:
            raise DispatchLedgerError("INTENT_REVOKED")
        prior = self._intents.get(intent.intent_id)
        if prior is not None:
            if prior == intent:
                return prior
            raise DispatchLedgerError("INTENT_CONFLICT")
        self._intents[intent.intent_id] = intent
        self._statuses[intent.intent_id] = READY
        self._history[intent.intent_id] = []
        return intent

    def claim(self, intent_id: str, *, now: datetime,
              lease_until: datetime) -> DispatchAttempt:
        """Take a lease-bounded attempt on a READY intent."""
        _utc(now, "CLAIM_NOW")
        _utc(lease_until, "LEASE_UNTIL")
        intent = self._intents.get(intent_id)
        if intent is None or intent_id in self._revoked:
            raise DispatchLedgerError("INTENT_UNKNOWN")
        if self._statuses.get(intent_id) != READY:
            raise DispatchLedgerError("INTENT_NOT_CLAIMABLE")
        if now < intent.created_at:
            raise DispatchLedgerError("CLAIM_WINDOW_INVALID")
        if not (now < lease_until):
            raise DispatchLedgerError("LEASE_WINDOW_INVALID")
        attempt_no = self._attempt_nos.get(intent_id, 0) + 1
        if attempt_no > _MAX_ATTEMPTS:
            raise DispatchLedgerError("ATTEMPTS_EXHAUSTED")
        attempt = DispatchAttempt(
            intent_id=intent_id, attempt_no=attempt_no, claimed_at=now,
            lease_until=lease_until, outcome=CLAIMED)
        self._current[intent_id] = attempt
        self._history[intent_id].append(attempt)
        self._attempt_nos[intent_id] = attempt_no
        self._statuses[intent_id] = CLAIMED
        return attempt

    def finish(self, attempt: DispatchAttempt, outcome: str, *, now: datetime,
               external_message_id: Optional[str] = None,
               observed_at: Optional[datetime] = None) -> DispatchAttempt:
        """Settle the current attempt; evidence mismatch forces UNKNOWN.

        ACCEPTED_VERIFIED / DEFINITELY_REJECTED require a non-empty
        external_message_id and an observed_at inside [claimed_at, now]
        while the lease is still open. UNKNOWN is terminal; only
        DEFINITELY_REJECTED returns the intent to READY (controlled retry).
        """
        _utc(now, "FINISH_NOW")
        if type(attempt) is not DispatchAttempt:
            raise DispatchLedgerError("ATTEMPT_INVALID")
        intent_id = attempt.intent_id
        current = self._current.get(intent_id)
        if (current is None or current != attempt
                or self._statuses.get(intent_id) != CLAIMED):
            raise DispatchLedgerError("ATTEMPT_NOT_CURRENT")
        if outcome not in _FINAL_OUTCOMES:
            raise DispatchLedgerError("OUTCOME_INVALID")
        final = outcome
        receipt_message: Optional[str] = None
        receipt_observed: Optional[datetime] = None
        if outcome in (ACCEPTED_VERIFIED, DEFINITELY_REJECTED):
            verified = now < attempt.lease_until
            if verified and (type(external_message_id) is not str
                             or not external_message_id
                             or len(external_message_id) > 128):
                verified = False
            if verified:
                try:
                    _utc(observed_at, "OBSERVED_AT")
                except DispatchLedgerError:
                    verified = False
            if verified and not (attempt.claimed_at <= observed_at <= now):
                verified = False
            if verified:
                receipt_message = external_message_id
                receipt_observed = observed_at
            else:
                # Receipt evidence inconsistent: fail closed to UNKNOWN.
                final = UNKNOWN
        finished = replace(attempt, outcome=final,
                           external_message_id=receipt_message,
                           observed_at=receipt_observed)
        self._current[intent_id] = finished
        self._history[intent_id][-1] = finished
        if final == DEFINITELY_REJECTED:
            self._statuses[intent_id] = READY
        else:
            self._statuses[intent_id] = final
        return finished

    def cancel(self, intent_id: str, *, now: datetime) -> bool:
        """Cancel only undelivered (READY) intents; claimed ones cannot."""
        _utc(now, "CANCEL_NOW")
        if self._statuses.get(intent_id) == READY:
            self._statuses[intent_id] = CANCELLED
            return True
        return False

    def reclaim_expired(self, intent_id: str, *, now: datetime) -> DispatchAttempt:
        """Explicit operator reclaim of one expired CLAIMED lease.

        Operator-invoked API only: it returns a CLAIMED intent whose lease
        has fully elapsed (now strictly greater than lease_until) to READY
        so a bounded fresh attempt can be taken. This is deliberately NOT
        an automatic path - it never changes the standing semantics that
        UNKNOWN is terminal and never auto-retried, never touches intents
        in any other state, never resets the 3-attempt budget, never bumps
        the control epoch and never rewrites the attempt history (the
        expired attempt record survives as CLAIMED; re-claims append).
        """
        _utc(now, "RECLAIM_NOW")
        if intent_id not in self._intents:
            raise DispatchLedgerError("INTENT_UNKNOWN")
        if self._statuses.get(intent_id) != CLAIMED:
            raise DispatchLedgerError("RECLAIM_STATE_INVALID")
        current = self._current.get(intent_id)
        if current is None or current.outcome != CLAIMED:
            raise DispatchLedgerError("RECLAIM_STATE_INVALID")
        if not (now > current.lease_until):
            raise DispatchLedgerError("RECLAIM_LEASE_ACTIVE")
        self._statuses[intent_id] = READY
        return replace(current, outcome=READY)

    def revoke(self, intent_id: str, *, now: datetime,
               epoch: Optional[int] = None) -> int:
        """Advance the control epoch; rollback is rejected fail-closed.

        READY intents become CANCELLED; CLAIMED attempts are forced to a
        terminal UNKNOWN with any receipt evidence dropped.
        """
        _utc(now, "REVOKE_NOW")
        if intent_id not in self._intents:
            raise DispatchLedgerError("INTENT_UNKNOWN")
        if epoch is not None:
            if type(epoch) is not int or epoch <= self._epoch:
                raise DispatchLedgerError("EPOCH_NOT_ADVANCED")
            self._epoch = epoch
        else:
            self._epoch += 1
        self._revoked.add(intent_id)
        status = self._statuses.get(intent_id)
        if status == READY:
            self._statuses[intent_id] = CANCELLED
        elif status == CLAIMED:
            current = self._current.get(intent_id)
            if current is not None and current.outcome == CLAIMED:
                finished = replace(current, outcome=UNKNOWN,
                                   external_message_id=None,
                                   observed_at=None)
                self._current[intent_id] = finished
                self._history[intent_id][-1] = finished
            self._statuses[intent_id] = UNKNOWN
        return self._epoch

    def get_intent(self, intent_id: str) -> Optional[DispatchIntent]:
        return self._intents.get(intent_id)

    def status(self, intent_id: str) -> Optional[str]:
        return self._statuses.get(intent_id)

    def get_attempts(self, intent_id: str) -> Tuple[DispatchAttempt, ...]:
        return tuple(self._history.get(intent_id, ()))

    def list_intents(self) -> Tuple[DispatchIntent, ...]:
        """Deterministic read-only snapshot of every planned intent."""
        def sort_key(intent: DispatchIntent) -> Tuple[str, str]:
            return (intent.created_at.isoformat(), intent.intent_id)

        return tuple(sorted(self._intents.values(), key=sort_key))
