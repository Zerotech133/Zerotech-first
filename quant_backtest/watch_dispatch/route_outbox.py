"""Offline single-upstream reporting outbox. No send, no trade, no persistence root.

Watch reports anomalies upward to the quant trading department only; the parent
layer owns any fan-out to Feishu, research or the trading desk. Each report
keeps an independent intent, attempt and receipt timeline. Unknown outcomes are
terminal here: only an independently verified definite rejection returns an
intent to READY_OFFLINE for a bounded controlled retry.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Dict, Tuple

from quant_backtest.watch_dispatch.canonical import digest, utc_text
from quant_backtest.watch_dispatch.contracts import EvidenceEnvelope, RouteIntent

READY_OFFLINE = "READY_OFFLINE"
CLAIMED = "CLAIMED"
ACCEPTED_VERIFIED = "ACCEPTED_VERIFIED"
DEFINITELY_REJECTED = "DEFINITELY_REJECTED"
UNKNOWN = "UNKNOWN"
CANCELLED = "CANCELLED"
_MAX_ATTEMPTS = 3


class OutboxError(ValueError):
    """Fail-closed outbox violation."""


@dataclass(frozen=True)
class RouteReceipt:
    route: str
    intent_id: str
    attempt_id: str
    external_message_id: str
    verification_source: str
    observed_at: datetime


@dataclass(frozen=True)
class RouteAttempt:
    attempt_id: str
    intent_id: str
    idempotency_key: str
    route: str
    attempt_no: int
    claim_at: datetime
    lease_until: datetime
    outcome: str
    receipt: RouteReceipt | None = None

    @property
    def production_permitted(self) -> bool:
        return False

    @property
    def notification_permitted(self) -> bool:
        return False

    @property
    def trading_permitted(self) -> bool:
        return False


def _utc(value: object, field: str) -> None:
    try:
        utc_text(value)  # type: ignore[arg-type]
    except ValueError as exc:
        raise OutboxError(field + "_INVALID") from exc


class RouteOutbox:
    """Per-target offline intent ledger; identical replan is idempotent."""

    def __init__(self) -> None:
        self._intents: Dict[str, RouteIntent] = {}
        self._identities: Dict[str, Tuple[str, ...]] = {}
        self._intent_ids: Dict[str, str] = {}
        self._attempts: Dict[str, RouteAttempt] = {}
        self._attempt_nos: Dict[str, int] = {}
        self._epochs: Dict[Tuple[str, ...], int] = {}
        self._revoked: set[str] = set()

    @property
    def production_permitted(self) -> bool:
        return False

    @property
    def notification_permitted(self) -> bool:
        return False

    @property
    def trading_permitted(self) -> bool:
        return False

    @staticmethod
    def _identity(envelope: EvidenceEnvelope) -> Tuple[str, ...]:
        return (envelope.pool, envelope.tenant_or_owner_id, envelope.market,
                envelope.code, envelope.event_id)

    def _key_for(self, envelope: EvidenceEnvelope, route: str,
                 target_type: str, target_id: str) -> str:
        draft = RouteIntent(
            intent_id="draft", event_id=envelope.event_id,
            evidence_version=envelope.evidence_version,
            envelope_hash=envelope.envelope_hash, route=route,
            target_type=target_type, target_id=target_id,
            tenant_scope=envelope.tenant_or_owner_id,
            payload_digest=envelope.envelope_hash, policy_version="draft",
            control_epoch=0, created_at=envelope.freeze_at,
            valid_until=envelope.valid_until)
        return draft.idempotency_key(envelope)

    def plan(self, envelope: EvidenceEnvelope, *, route: str, target_type: str,
             target_id: str, policy_version: str, control_epoch: int,
             intent_id: str, created_at: datetime,
             valid_until: datetime) -> RouteIntent:
        envelope.validate(now=created_at)
        _utc(created_at, "CREATED_AT")
        _utc(valid_until, "VALID_UNTIL")
        if not (envelope.freeze_at <= created_at < valid_until <= envelope.valid_until):
            raise OutboxError("ROUTE_TIME_INVALID")
        identity = self._identity(envelope)
        floor = self._epochs.get(identity)
        if floor is not None and control_epoch < floor:
            raise OutboxError("EPOCH_STALE")
        if identity in [self._identities.get(k) for k in self._revoked] and control_epoch <= (floor or 0):
            raise OutboxError("EPOCH_STALE")
        try:
            intent = RouteIntent(
                intent_id=intent_id, event_id=envelope.event_id,
                evidence_version=envelope.evidence_version,
                envelope_hash=envelope.envelope_hash, route=route,
                target_type=target_type, target_id=target_id,
                tenant_scope=envelope.tenant_or_owner_id,
                payload_digest=envelope.envelope_hash, policy_version=policy_version,
                control_epoch=control_epoch, created_at=created_at,
                valid_until=valid_until)
            intent.validate(envelope, now=created_at)
        except OutboxError:
            raise
        except ValueError as exc:
            raise OutboxError(str(exc)) from exc
        key = intent.idempotency_key(envelope)
        prior = self._intent_ids.get(intent.intent_id)
        if prior is not None and prior != key:
            raise OutboxError("INTENT_ID_CONFLICT")
        existing = self._intents.get(key)
        if existing is not None:
            if existing == intent:
                return existing
            raise OutboxError("INTENT_CONFLICT")
        if floor is None or control_epoch > floor:
            self._epochs[identity] = control_epoch
        self._intents[key] = intent
        self._identities[key] = identity
        self._intent_ids[intent.intent_id] = key
        return intent

    def claim(self, envelope: EvidenceEnvelope, *, route: str, target_type: str,
              target_id: str, claim_token: str, claim_at: datetime,
              lease_until: datetime) -> RouteAttempt:
        envelope.validate(now=claim_at)
        _utc(claim_at, "CLAIM_AT")
        _utc(lease_until, "LEASE_UNTIL")
        if type(claim_token) is not str or not claim_token or len(claim_token) > 128:
            raise OutboxError("CLAIM_TOKEN_INVALID")
        key = self._key_for(envelope, route, target_type, target_id)
        intent = self._intents.get(key)
        if intent is None:
            raise OutboxError("INTENT_UNKNOWN")
        if key in self._revoked or intent.control_epoch < self._epochs.get(self._identities[key], intent.control_epoch):
            raise OutboxError("INTENT_REVOKED")
        if intent.status != READY_OFFLINE:
            raise OutboxError("INTENT_NOT_CLAIMABLE")
        if not (intent.created_at <= claim_at < intent.valid_until):
            raise OutboxError("CLAIM_WINDOW_INVALID")
        if not (claim_at < lease_until <= intent.valid_until):
            raise OutboxError("LEASE_WINDOW_INVALID")
        attempt_no = self._attempt_nos.get(key, 0) + 1
        if attempt_no > _MAX_ATTEMPTS:
            raise OutboxError("ATTEMPTS_EXHAUSTED")
        attempt_id = digest([key, attempt_no, claim_token, utc_text(claim_at)])
        if attempt_id in self._attempts:
            raise OutboxError("CLAIM_TOKEN_REUSED")
        attempt = RouteAttempt(
            attempt_id=attempt_id, intent_id=intent.intent_id,
            idempotency_key=key, route=intent.route, attempt_no=attempt_no,
            claim_at=claim_at, lease_until=lease_until, outcome=CLAIMED)
        self._attempts[attempt_id] = attempt
        self._attempt_nos[key] = attempt_no
        self._intents[key] = replace(intent, status=CLAIMED)
        return attempt

    def finish(self, attempt_id: str, *, outcome: str, now: datetime,
               external_message_id: str | None = None,
               verification_source: str | None = None,
               observed_at: datetime | None = None) -> RouteAttempt:
        _utc(now, "FINISH_NOW")
        attempt = self._attempts.get(attempt_id)
        if attempt is None or attempt.outcome != CLAIMED:
            raise OutboxError("ATTEMPT_UNKNOWN")
        intent = self._intents.get(attempt.idempotency_key)
        if intent is None or intent.status != CLAIMED or intent.intent_id != attempt.intent_id:
            raise OutboxError("ATTEMPT_NOT_CURRENT")
        if outcome not in (ACCEPTED_VERIFIED, DEFINITELY_REJECTED, UNKNOWN):
            raise OutboxError("OUTCOME_INVALID")
        expired = now >= attempt.lease_until
        stale = not (intent.created_at <= now < intent.valid_until)
        receipt = None
        if outcome in (ACCEPTED_VERIFIED, DEFINITELY_REJECTED) and not expired and not stale:
            if (type(external_message_id) is str and external_message_id
                    and type(verification_source) is str and verification_source
                    and observed_at is not None):
                _utc(observed_at, "OBSERVED_AT")
                if attempt.claim_at <= observed_at <= now:
                    receipt = RouteReceipt(
                        route=intent.route, intent_id=intent.intent_id,
                        attempt_id=attempt_id,
                        external_message_id=external_message_id,
                        verification_source=verification_source,
                        observed_at=observed_at)
        if receipt is None:
            outcome = UNKNOWN
        updated = replace(attempt, outcome=outcome, receipt=receipt)
        self._attempts[attempt_id] = updated
        if outcome == DEFINITELY_REJECTED:
            self._intents[attempt.idempotency_key] = replace(intent, status=READY_OFFLINE)
        else:
            self._intents[attempt.idempotency_key] = replace(intent, status=outcome)
        return updated

    def cancel(self, envelope: EvidenceEnvelope, *, route: str, target_type: str,
               target_id: str, now: datetime) -> bool:
        """Cancel only undelivered intents; claimed ones need manual reconciliation."""
        _utc(now, "CANCEL_NOW")
        envelope.validate(now=now)
        key = self._key_for(envelope, route, target_type, target_id)
        intent = self._intents.get(key)
        if intent is None or key in self._revoked:
            return False
        if intent.status == READY_OFFLINE:
            self._intents[key] = replace(intent, status=CANCELLED)
            return True
        return False

    def revoke_envelope(self, envelope: EvidenceEnvelope, *, epoch: int,
                        now: datetime) -> int:
        """Advance the control epoch: undelivered intents cancel, claimed go UNKNOWN."""
        _utc(now, "REVOKE_NOW")
        if type(epoch) is not int or epoch < 0:
            raise OutboxError("EPOCH_INVALID")
        identity = self._identity(envelope)
        current = self._epochs.get(identity)
        if current is not None and epoch <= current:
            raise OutboxError("EPOCH_NOT_ADVANCED")
        self._epochs[identity] = epoch
        for key, known in self._identities.items():
            if known != identity or key in self._revoked:
                continue
            self._revoked.add(key)
            intent = self._intents[key]
            if intent.status == READY_OFFLINE:
                self._intents[key] = replace(intent, status=CANCELLED)
            elif intent.status == CLAIMED:
                self._intents[key] = replace(intent, status=UNKNOWN)
        return epoch

    def get_intent(self, envelope: EvidenceEnvelope, *, route: str,
                   target_type: str, target_id: str) -> RouteIntent | None:
        return self._intents.get(self._key_for(envelope, route, target_type, target_id))

    def get_attempts(self, envelope: EvidenceEnvelope, *, route: str,
                     target_type: str, target_id: str) -> tuple[RouteAttempt, ...]:
        key = self._key_for(envelope, route, target_type, target_id)
        found = [a for a in self._attempts.values() if a.idempotency_key == key]
        return tuple(sorted(found, key=lambda a: a.attempt_no))
