"""Offline single-upstream reporting outbox. No send, no trade, no persistence root.

Watch reports anomalies upward to the quant trading department only; the parent
layer owns any fan-out to Feishu, research or the trading desk. Each report
keeps an independent intent, attempt and receipt timeline. Unknown attempts
remain terminal; a new explicitly planned generation can supersede an unknown
intent while sharing its bounded attempt budget. Only an independently verified
definite rejection reopens the same generation.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Dict, Protocol, Tuple

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
class ReportFingerprint:
    """Immutable identity of a sector report across intent generations."""

    report_id: str
    report_kind: str
    event_id: str
    evidence_version: int
    envelope_hash: str
    sender_agent_id: str
    receiver_agent_id: str
    created_at: datetime


class ActiveAckPort(Protocol):
    """Construction-bound authority for a live, exact upstream ACK."""

    def active_ack(self, fingerprint: ReportFingerprint, ack_id: str,
                   verification_source: str, acked_at: datetime, *,
                   now: datetime) -> bool: ...


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

    def __init__(self, active_ack_port: ActiveAckPort | None = None) -> None:
        self._active_ack_port = active_ack_port
        self._intents: Dict[str, RouteIntent] = {}
        self._generations: Dict[str, int] = {}
        self._intent_history: Dict[str, list[RouteIntent]] = {}
        self._origin_created_at: Dict[str, datetime] = {}
        self._report_fingerprints: Dict[str, ReportFingerprint] = {}
        self._identities: Dict[str, Tuple[str, ...]] = {}
        self._intent_ids: Dict[str, str] = {}
        self._attempts: Dict[str, RouteAttempt] = {}
        self._attempt_nos: Dict[str, int] = {}
        self._epochs: Dict[Tuple[str, ...], int] = {}
        self._revoked: set[str] = set()
        self._revoked_identities: set[Tuple[str, ...]] = set()

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

    def epoch_floor(self, envelope: EvidenceEnvelope) -> int:
        """Return the current control-epoch lower bound for this identity."""
        return self._epochs.get(self._identity(envelope), 0)

    def plan(self, envelope: EvidenceEnvelope, *, route: str, target_type: str,
             target_id: str, policy_version: str, control_epoch: int,
             intent_id: str, created_at: datetime,
             valid_until: datetime, generation: int = 0,
             report_fingerprint: ReportFingerprint | None = None) -> RouteIntent:
        envelope.validate(now=created_at)
        if type(generation) is not int or generation < 0:
            raise OutboxError("RETRY_GENERATION_INVALID")
        _utc(created_at, "CREATED_AT")
        _utc(valid_until, "VALID_UNTIL")
        if not (envelope.freeze_at <= created_at < valid_until <= envelope.valid_until):
            raise OutboxError("ROUTE_TIME_INVALID")
        identity = self._identity(envelope)
        floor = self._epochs.get(identity)
        if floor is not None and control_epoch < floor:
            raise OutboxError("EPOCH_STALE")
        if identity in self._revoked_identities and control_epoch <= (floor or 0):
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
        if key in self._revoked:
            raise OutboxError("RETRY_KEY_REVOKED")
        if report_fingerprint is not None:
            if (type(report_fingerprint) is not ReportFingerprint
                    or route != "QUANT_UPSTREAM_REPORT"
                    or report_fingerprint.event_id != envelope.event_id
                    or report_fingerprint.evidence_version != envelope.evidence_version
                    or report_fingerprint.envelope_hash != envelope.envelope_hash
                    or report_fingerprint.receiver_agent_id != target_id
                    or (generation == 0
                        and report_fingerprint.created_at != created_at)
                    or (generation > 0
                        and report_fingerprint.created_at > created_at)
                    or type(report_fingerprint.report_id) is not str
                    or not report_fingerprint.report_id
                    or len(report_fingerprint.report_id) > 128
                    or type(report_fingerprint.report_kind) is not str
                    or report_fingerprint.report_kind not in ("ANOMALY", "HEALTH")
                    or type(report_fingerprint.sender_agent_id) is not str
                    or not report_fingerprint.sender_agent_id
                    or len(report_fingerprint.sender_agent_id) > 128):
                raise OutboxError("REPORT_FINGERPRINT_MISMATCH")
            _utc(report_fingerprint.created_at, "REPORT_CREATED_AT")
        existing = self._intents.get(key)
        if existing is not None:
            current_generation = self._generations[key]
            if existing == intent and generation == current_generation:
                original = self._report_fingerprints.get(key)
                if generation > 0 and original is None:
                    raise OutboxError("REPORT_FINGERPRINT_REQUIRED")
                if generation > 0 and report_fingerprint is None:
                    raise OutboxError("REPORT_FINGERPRINT_REQUIRED")
                if (report_fingerprint is not None
                        and original != report_fingerprint):
                    raise OutboxError("REPORT_FINGERPRINT_MISMATCH")
                return existing
            if existing.status != UNKNOWN or generation == 0:
                raise OutboxError("INTENT_CONFLICT")
            if (self._attempt_nos.get(key, 0) == 0
                    or not any(a.idempotency_key == key and a.outcome == UNKNOWN
                               and a.intent_id == existing.intent_id
                               for a in self._attempts.values())):
                raise OutboxError("RETRY_NOT_PERMITTED")
            if generation != current_generation + 1:
                raise OutboxError("RETRY_GENERATION_INVALID")
            if prior is not None:
                raise OutboxError("INTENT_ID_REUSE")
            first_intent = (self._intent_history[key][0]
                            if self._intent_history[key] else existing)
            if control_epoch != first_intent.control_epoch:
                raise OutboxError("RETRY_EPOCH_CHANGED")
            if created_at <= existing.created_at:
                raise OutboxError("ROUTE_TIME_INVALID")
            original = self._report_fingerprints.get(key)
            if original is None or report_fingerprint is None:
                raise OutboxError("REPORT_FINGERPRINT_REQUIRED")
            if report_fingerprint != original:
                raise OutboxError("REPORT_FINGERPRINT_MISMATCH")
            self._intent_history[key].append(existing)
        elif generation != 0:
            raise OutboxError("RETRY_GENERATION_INVALID")
        if floor is None or control_epoch > floor:
            self._epochs[identity] = control_epoch
        self._intents[key] = intent
        self._generations[key] = generation
        self._intent_history.setdefault(key, [])
        self._origin_created_at.setdefault(key, created_at)
        if report_fingerprint is not None and generation == 0:
            self._report_fingerprints[key] = report_fingerprint
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
        if attempt is None:
            raise OutboxError("ATTEMPT_UNKNOWN")
        if attempt.idempotency_key in self._revoked:
            raise OutboxError("ATTEMPT_NOT_CURRENT")
        if attempt.outcome != CLAIMED:
            raise OutboxError("ATTEMPT_UNKNOWN")
        intent = self._intents.get(attempt.idempotency_key)
        if intent is None or intent.status != CLAIMED or intent.intent_id != attempt.intent_id:
            raise OutboxError("ATTEMPT_NOT_CURRENT")
        if outcome not in (ACCEPTED_VERIFIED, DEFINITELY_REJECTED, UNKNOWN):
            raise OutboxError("OUTCOME_INVALID")
        expired = now >= attempt.lease_until
        stale = not (attempt.claim_at <= now < intent.valid_until)
        receipt = None
        if outcome in (ACCEPTED_VERIFIED, DEFINITELY_REJECTED) and not expired and not stale:
            if (type(external_message_id) is str and external_message_id
                    and type(verification_source) is str and verification_source
                    and observed_at is not None):
                _utc(observed_at, "OBSERVED_AT")
                current_window = intent.created_at <= observed_at <= now
                if outcome == ACCEPTED_VERIFIED and intent.route == "QUANT_UPSTREAM_REPORT":
                    key = attempt.idempotency_key
                    fingerprint = self._report_fingerprints.get(key)
                    history = self._intent_history.get(key, [])
                    identity = self._identities.get(key)
                    generation = self._generations.get(key, 0)
                    original_created_at = self._origin_created_at.get(key)
                    original_matches = (
                        fingerprint is not None
                        and original_created_at == fingerprint.created_at
                        and (len(history) == 0
                             and fingerprint.created_at == intent.created_at
                             if generation == 0 else
                             len(history) == generation
                             and fingerprint.created_at == history[0].created_at)
                    )
                    within_report_window = fingerprint is not None and (
                        fingerprint.created_at <= observed_at <= now)
                    trusted_context = (
                        identity is not None and key not in self._revoked
                        and self._active_ack_port is not None
                        and all(previous.control_epoch == intent.control_epoch
                                for previous in history)
                        and intent.control_epoch == self._epochs.get(identity)
                    )
                    if (original_matches and within_report_window and trusted_context
                            and (current_window or generation > 0)):
                        try:
                            current_window = (self._active_ack_port.active_ack(
                                fingerprint, external_message_id,
                                verification_source, observed_at, now=now) is True)
                        except Exception:
                            current_window = False
                        # The authority callback may revoke this claim or advance its epoch.
                        current_attempt = self._attempts.get(attempt_id)
                        current_intent = self._intents.get(key)
                        if (current_attempt is not attempt
                                or current_intent is not intent
                                or key in self._revoked
                                or self._identities.get(key) != identity
                                or self._epochs.get(identity) != intent.control_epoch
                                or self._generations.get(key, 0) != generation
                                or self._report_fingerprints.get(key) != fingerprint):
                            updated = replace(attempt, outcome=UNKNOWN, receipt=None)
                            # Do not overwrite a concurrent revocation or replacement.
                            if current_attempt is attempt:
                                self._attempts[attempt_id] = updated
                            if current_intent is intent:
                                self._intents[key] = replace(intent, status=UNKNOWN)
                            return updated
                    else:
                        current_window = False
                if current_window:
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
        affected = [(key, self._intents[key]) for key, known in self._identities.items()
                    if known == identity and key not in self._revoked]
        active: Dict[str, str] = {}
        for key, intent in affected:
            if intent.status != CLAIMED:
                continue
            matches = [attempt for attempt in self._attempts.values()
                       if attempt.idempotency_key == key and attempt.outcome == CLAIMED]
            if (len(matches) != 1 or matches[0].intent_id != intent.intent_id):
                raise OutboxError("REVOKE_ATTEMPT_INCONSISTENT")
            active[key] = matches[0].attempt_id
        self._epochs[identity] = epoch
        self._revoked_identities.add(identity)
        for key, intent in affected:
            self._revoked.add(key)
            if intent.status == READY_OFFLINE:
                self._intents[key] = replace(intent, status=CANCELLED)
            elif intent.status == CLAIMED:
                self._intents[key] = replace(intent, status=UNKNOWN)
                attempt_id = active[key]
                self._attempts[attempt_id] = replace(
                    self._attempts[attempt_id], outcome=UNKNOWN, receipt=None)
        return epoch

    def get_intent(self, envelope: EvidenceEnvelope, *, route: str,
                   target_type: str, target_id: str) -> RouteIntent | None:
        return self._intents.get(self._key_for(envelope, route, target_type, target_id))

    def get_attempts(self, envelope: EvidenceEnvelope, *, route: str,
                     target_type: str, target_id: str) -> tuple[RouteAttempt, ...]:
        key = self._key_for(envelope, route, target_type, target_id)
        found = [a for a in self._attempts.values() if a.idempotency_key == key]
        return tuple(sorted(found, key=lambda a: a.attempt_no))
