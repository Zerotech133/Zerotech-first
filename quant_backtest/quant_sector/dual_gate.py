"""Offline dual-gate permission state machine. No dispatch adapter is reachable.

Human authorization and an independent RiskGuard PASS are two separately
verified gates. Research reviews, notification ACKs and trader acknowledgements
never substitute for either. Even a fully eligible verdict cannot dispatch:
the exchange port is disabled in this offline slice.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, Protocol, Tuple

from quant_backtest.watch_dispatch.canonical import utc_text
from quant_backtest.watch_dispatch.contracts import EvidenceEnvelope

_OBSERVE_ONLY = "OBSERVE_ONLY"
_REVIEW_CONFIRMED = "REVIEW_CONFIRMED"
_BLOCKED = "BLOCKED"
_SIM_DISPATCH_ELIGIBLE = "SIM_DISPATCH_ELIGIBLE"
_SIDES = frozenset(("BUY", "SELL"))
_REVIEW_DECISIONS = frozenset(("CONFIRMED", "REJECTED", "NEEDS_EVIDENCE"))
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")


class GateError(ValueError):
    """Fail-closed gate violation."""


def _label(value: object, field: str) -> None:
    if type(value) is not str or _LABEL.fullmatch(value) is None:
        raise GateError(field + "_INVALID")


def _utc(value: object, field: str) -> None:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise GateError(field + "_INVALID")
    utc_text(value)  # type: ignore[arg-type]


@dataclass(frozen=True)
class AuthorizationScope:
    market: str
    code: str
    side: str
    max_quantity: int
    sim_only: bool

    def __post_init__(self) -> None:
        _label(self.market, "SCOPE_MARKET")
        _label(self.code, "SCOPE_CODE")
        if self.side not in _SIDES or type(self.side) is not str:
            raise GateError("SCOPE_SIDE_INVALID")
        if type(self.max_quantity) is not int or self.max_quantity <= 0:
            raise GateError("SCOPE_QUANTITY_INVALID")
        if self.sim_only is not True:
            raise GateError("SCOPE_SIM_ONLY_REQUIRED")

    def covers(self, envelope: EvidenceEnvelope) -> bool:
        return self.market == envelope.market and self.code == envelope.code


@dataclass(frozen=True)
class HumanAuthorization:
    authorization_id: str
    event_id: str
    evidence_version: int
    envelope_hash: str
    principal_id: str
    external_authority_ref: str
    auth_epoch: int
    review_id: str | None
    scope: AuthorizationScope
    issued_at: datetime
    valid_from: datetime
    valid_until: datetime

    @property
    def dispatch_permitted(self) -> bool:
        return False

    @property
    def trading_permitted(self) -> bool:
        return False

    def __post_init__(self) -> None:
        for name in ("authorization_id", "event_id", "principal_id", "external_authority_ref"):
            _label(getattr(self, name), name.upper())
        if type(self.evidence_version) is not int or self.evidence_version < 1:
            raise GateError("VERSION_INVALID")
        if type(self.envelope_hash) is not str or len(self.envelope_hash) != 64:
            raise GateError("ENVELOPE_HASH_INVALID")
        if type(self.auth_epoch) is not int or self.auth_epoch < 0:
            raise GateError("EPOCH_INVALID")
        if self.review_id is not None:
            _label(self.review_id, "REVIEW_ID")
        if type(self.scope) is not AuthorizationScope:
            raise GateError("SCOPE_INVALID")
        for name in ("issued_at", "valid_from", "valid_until"):
            _utc(getattr(self, name), name.upper())
        if not (self.issued_at <= self.valid_from < self.valid_until):
            raise GateError("AUTH_TIME_ORDER_INVALID")

    def binds(self, envelope: EvidenceEnvelope) -> bool:
        return (self.event_id == envelope.event_id
                and self.evidence_version == envelope.evidence_version
                and self.envelope_hash == envelope.envelope_hash)


@dataclass(frozen=True)
class RiskDecision:
    decision_id: str
    event_id: str
    evidence_version: int
    envelope_hash: str
    authorization_id: str
    decision: str
    policy_version: str
    as_of: datetime
    valid_until: datetime
    external_verification_ref: str

    @property
    def dispatch_permitted(self) -> bool:
        return False

    @property
    def trading_permitted(self) -> bool:
        return False

    def __post_init__(self) -> None:
        for name in ("decision_id", "event_id", "authorization_id", "policy_version",
                     "external_verification_ref"):
            _label(getattr(self, name), name.upper())
        if self.decision not in ("PASS", "DENY") or type(self.decision) is not str:
            raise GateError("RISK_DECISION_INVALID")
        if type(self.evidence_version) is not int or self.evidence_version < 1:
            raise GateError("VERSION_INVALID")
        if type(self.envelope_hash) is not str or len(self.envelope_hash) != 64:
            raise GateError("ENVELOPE_HASH_INVALID")
        _utc(self.as_of, "RISK_AS_OF")
        _utc(self.valid_until, "RISK_VALID_UNTIL")
        if not (self.as_of < self.valid_until):
            raise GateError("RISK_TIME_ORDER_INVALID")

    def binds(self, envelope: EvidenceEnvelope) -> bool:
        return (self.event_id == envelope.event_id
                and self.evidence_version == envelope.evidence_version
                and self.envelope_hash == envelope.envelope_hash)


@dataclass(frozen=True)
class ResearchReview:
    review_id: str
    event_id: str
    evidence_version: int
    envelope_hash: str
    reviewer_external_identity: str
    decision: str
    recorded_at: datetime

    def __post_init__(self) -> None:
        for name in ("review_id", "event_id", "reviewer_external_identity"):
            _label(getattr(self, name), name.upper())
        if self.decision not in _REVIEW_DECISIONS or type(self.decision) is not str:
            raise GateError("REVIEW_DECISION_INVALID")
        if type(self.evidence_version) is not int or self.evidence_version < 1:
            raise GateError("VERSION_INVALID")
        if type(self.envelope_hash) is not str or len(self.envelope_hash) != 64:
            raise GateError("ENVELOPE_HASH_INVALID")
        _utc(self.recorded_at, "RECORDED_AT")

    def binds(self, envelope: EvidenceEnvelope) -> bool:
        return (self.event_id == envelope.event_id
                and self.evidence_version == envelope.evidence_version
                and self.envelope_hash == envelope.envelope_hash)


@dataclass(frozen=True)
class DispatchVerdict:
    """Diagnostic only: no verdict can itself open the disabled dispatch port."""

    status: str
    code: str
    authorization: HumanAuthorization | None = None
    risk: RiskDecision | None = None

    @property
    def dispatch_permitted(self) -> bool:
        return False

    @property
    def trading_permitted(self) -> bool:
        return False

    @property
    def notification_permitted(self) -> bool:
        return False

    @property
    def production_permitted(self) -> bool:
        return False


class AuthorizationAuthority(Protocol):
    """Independent persistence/revocation verifier; may not trust the caller."""

    def resolve_and_verify(self, authorization_id: str, envelope: EvidenceEnvelope,
                           now: datetime) -> HumanAuthorization | None: ...


class RiskGuardPort(Protocol):
    """Independent risk evaluation from inputs visible at `now`."""

    def evaluate_independently(self, envelope: EvidenceEnvelope,
                               authorization: HumanAuthorization,
                               now: datetime) -> RiskDecision: ...


def _blocked(code: str) -> DispatchVerdict:
    return DispatchVerdict(status=_BLOCKED, code=code)


class PermissionGate:
    """Per-envelope identity state machine; every consume re-verifies both gates."""

    def __init__(self, acceptance_lookup: Callable[[str], bool] | None = None) -> None:
        """Optional upstream-acceptance lookup; None keeps consume closed on failure.

        The lookup receives the acceptance reference (e.g. the quant-sector
        UpstreamInbox receipt_id) and must answer whether that acceptance is
        still active; revoked or unknown references must yield False. Lookup
        exceptions propagate to the caller; the gate never opens when the
        lookup raises; consumers must handle the error path.
        """
        self._acceptance_lookup = acceptance_lookup
        self._reviews: Dict[str, ResearchReview] = {}
        self._authorizations: Dict[str, HumanAuthorization] = {}
        self._epochs: Dict[Tuple[str, ...], int] = {}
        self._blocked: set[Tuple[str, ...]] = set()

    @property
    def dispatch_permitted(self) -> bool:
        return False

    @property
    def trading_permitted(self) -> bool:
        return False

    @staticmethod
    def _identity(envelope: EvidenceEnvelope) -> Tuple[str, ...]:
        return (envelope.pool, envelope.tenant_or_owner_id, envelope.market,
                envelope.code, envelope.event_id)

    def _floor(self, envelope: EvidenceEnvelope) -> int:
        return self._epochs.get(self._identity(envelope), 0)

    def submit_review(self, envelope: EvidenceEnvelope,
                      review: ResearchReview) -> str:
        envelope.validate(now=review.recorded_at)
        if not review.binds(envelope):
            raise GateError("REVIEW_IDENTITY_MISMATCH")
        if review.review_id in self._reviews:
            raise GateError("REVIEW_EXISTS")
        identity = self._identity(envelope)
        if identity in self._blocked:
            raise GateError("IDENTITY_BLOCKED")
        self._reviews[review.review_id] = review
        if review.decision == "REJECTED":
            self._blocked.add(identity)
            return _BLOCKED
        return _REVIEW_CONFIRMED if review.decision == "CONFIRMED" else _OBSERVE_ONLY

    def record_authorization(self, envelope: EvidenceEnvelope,
                             authorization: HumanAuthorization) -> None:
        if not authorization.binds(envelope):
            raise GateError("AUTH_IDENTITY_MISMATCH")
        if not authorization.scope.covers(envelope):
            raise GateError("AUTH_SCOPE_MISMATCH")
        identity = self._identity(envelope)
        if identity in self._blocked:
            raise GateError("IDENTITY_BLOCKED")
        if authorization.auth_epoch < self._floor(envelope):
            raise GateError("EPOCH_STALE")
        if not (envelope.freeze_at <= authorization.valid_from
                and authorization.valid_until <= envelope.valid_until):
            raise GateError("AUTH_WINDOW_INVALID")
        confirmed = any(r.binds(envelope) and r.decision == "CONFIRMED"
                        and (authorization.review_id is None
                             or r.review_id == authorization.review_id)
                        for r in self._reviews.values())
        if not confirmed:
            raise GateError("AUTH_REQUIRES_CONFIRMED_REVIEW")
        if authorization.authorization_id in self._authorizations:
            raise GateError("AUTHORIZATION_EXISTS")
        self._authorizations[authorization.authorization_id] = authorization
        if authorization.auth_epoch > self._floor(envelope):
            self._epochs[identity] = authorization.auth_epoch

    def revoke_envelope(self, envelope: EvidenceEnvelope, *, epoch: int,
                        now: datetime) -> None:
        _utc(now, "REVOKE_NOW")
        if type(epoch) is not int or epoch < 0:
            raise GateError("EPOCH_INVALID")
        identity = self._identity(envelope)
        if epoch <= self._floor(envelope):
            raise GateError("EPOCH_NOT_ADVANCED")
        self._epochs[identity] = epoch
        self._blocked.add(identity)

    def consume(self, envelope: EvidenceEnvelope, *, authorization_id: str,
                now: datetime, authority_port: AuthorizationAuthority,
                risk_port: RiskGuardPort,
                acceptance_ref: str | None = None) -> DispatchVerdict:
        """Both gates are re-verified live; stale records never reopen a gate.

        The upstream-acceptance precondition runs before both gates: only an
        injected acceptance_lookup that resolves acceptance_ref to an active
        acceptance lets evaluation proceed; no lookup, an unknown reference
        or a revoked acceptance all yield BLOCKED/UPSTREAM_NOT_ACCEPTED.
        """
        _utc(now, "CONSUME_NOW")
        try:
            envelope.validate(now=now)
        except ValueError:
            return _blocked("EVIDENCE_NOT_CURRENT")
        identity = self._identity(envelope)
        if identity in self._blocked:
            return _blocked("IDENTITY_BLOCKED")
        # Upstream-acceptance precondition: closed on failure before both gates.
        # Never reported upstream, not yet accepted, or acceptance revoked:
        # in every such case the event identity must not be consumed.
        if (self._acceptance_lookup is None
                or type(acceptance_ref) is not str
                or not self._acceptance_lookup(acceptance_ref)):
            return _blocked("UPSTREAM_NOT_ACCEPTED")
        authorization = authority_port.resolve_and_verify(authorization_id, envelope, now)
        if authorization is None:
            return _blocked("AUTH_UNVERIFIED")
        if not authorization.binds(envelope) or authorization.authorization_id != authorization_id:
            return _blocked("AUTH_IDENTITY_MISMATCH")
        if not (authorization.valid_from <= now < authorization.valid_until):
            return _blocked("AUTH_NOT_CURRENT")
        if not authorization.scope.covers(envelope) or authorization.scope.sim_only is not True:
            return _blocked("AUTH_SCOPE_MISMATCH")
        risk = risk_port.evaluate_independently(envelope, authorization, now)
        if risk is None:
            return _blocked("RISK_UNVERIFIED")
        if not risk.binds(envelope) or risk.authorization_id != authorization.authorization_id:
            return _blocked("RISK_IDENTITY_MISMATCH")
        if risk.decision != "PASS":
            return _blocked("RISK_DENY")
        if not (risk.as_of <= now < risk.valid_until):
            return _blocked("RISK_NOT_CURRENT")
        return DispatchVerdict(status=_SIM_DISPATCH_ELIGIBLE, code="GATES_PASS",
                               authorization=authorization, risk=risk)
