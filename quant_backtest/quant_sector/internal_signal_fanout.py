"""Offline, in-memory internal signal fanout; production settlement BLOCKED.

This is a logical two-team simulation, not an external delivery service. A
LOGICAL_RECEIVED state means only that the authorised internal consumer called
finish; it is not an external ACK, research approval, trading authorisation,
order or fill. No network or disk I/O occurs here. Only ANOMALY signals
enter these business subscriptions; HEALTH is an upstream report, not a
research or trader task. Claimed leases and FAILED/UNKNOWN responses are
terminal in this slice; reclaim/retry requires a separately designed G2-P1
recovery contract and must never be inferred from logical receipt state.

The injected acceptance and permission ports are mandatory. Reads followed by
writes across the upstream inbox and this independent memory ledger are NOT an
atomic fence: a concurrent upstream revocation can intervene even after the
last check. Production settlement/real connection remains BLOCKED until the
unique durable owner, shared transaction or durable CAS, trusted clock and
revocation linearisation have been approved and implemented.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Dict, Optional, Tuple

from quant_backtest.quant_sector.upstream_inbox import (
    QuantUpstreamReport,
    UpstreamReceipt,
    digest,
)
from quant_backtest.watch_dispatch.signal_pool import SignalPoolEntry, STATUS_REVOKED

_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
RESEARCH = "RESEARCH"
TRADING = "TRADING"
_TEAMS = (RESEARCH, TRADING)
_ROUTES = {RESEARCH: "RESEARCH_REVIEW", TRADING: "EXCHANGE_OBSERVATION"}
PLANNED = "PLANNED"
CLAIMED = "CLAIMED"
LOGICAL_RECEIVED = "LOGICAL_RECEIVED"
FAILED = "FAILED"
UNKNOWN = "UNKNOWN"
_REPLIES = {RESEARCH: frozenset(("RESEARCH_INTENT", "RESEARCH_DECLINED")),
            TRADING: frozenset(("TRADER_OBSERVE", "TRADER_DECLINED"))}


class InternalFanoutError(ValueError):
    """A fail-closed internal fanout contract violation."""


def _label(value: object, field: str) -> None:
    if type(value) is not str or _LABEL.fullmatch(value) is None:
        raise InternalFanoutError(field + "_INVALID")


def _utc(value: object, field: str) -> None:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise InternalFanoutError(field + "_INVALID")


@dataclass(frozen=True)
class InternalSubscription:
    """One independent internal, non-executable subscription per team."""

    signal_id: str
    report_id: str
    receipt_id: str
    team: str
    route: str
    consumer_identity: str
    planned_at: datetime
    subscription_id: str


@dataclass(frozen=True)
class InternalClaim:
    """A lease-bounded local opportunity to record logical consumption."""

    subscription_id: str
    consumer_identity: str
    claimed_at: datetime
    lease_until: datetime
    claim_id: str


@dataclass(frozen=True)
class InternalResponse:
    """An internal logical result, never an ACK or an execution event."""

    subscription_id: str
    team: str
    consumer_identity: str
    outcome: str
    recorded_at: datetime
    reply: Optional[str] = None


@dataclass(frozen=True)
class FanoutResult:
    """Independent results: failure of one team does not imply the other failed."""

    research: Optional[InternalSubscription]
    trading: Optional[InternalSubscription]
    errors: Tuple[Tuple[str, str], ...]


class InternalSignalFanout:
    """In-memory fanout with separately validated team-specific state.

    acceptance_lookup must retrieve the authoritative current inbox receipt;
    acceptance_is_active must independently check its current non-revoked
    status. Bind both to the same trusted UpstreamInbox instance (get and
    is_active), never to a caller-provided receipt or constant-True lambda.
    signal_lookup must read the separately identified pool entry from its
    trusted pool, never synthesize it from the caller's report or signal_id.
    consumer_authorized is a separate trusted identity/permission decision.
    None of these injected ports makes independent ledgers atomic.
    """

    def __init__(
        self,
        *,
        acceptance_lookup: Callable[[str], Optional[UpstreamReceipt]],
        acceptance_is_active: Callable[[str], bool],
        signal_lookup: Callable[[str, datetime], Optional[SignalPoolEntry]],
        consumer_authorized: Callable[[str, str, datetime], bool],
    ) -> None:
        if not all(callable(port) for port in (
                acceptance_lookup, acceptance_is_active, signal_lookup,
                consumer_authorized)):
            raise InternalFanoutError("TRUST_PORT_REQUIRED")
        self._acceptance_lookup = acceptance_lookup
        self._acceptance_is_active = acceptance_is_active
        self._signal_lookup = signal_lookup
        self._consumer_authorized = consumer_authorized
        self._subscriptions: Dict[Tuple[str, str], InternalSubscription] = {}
        self._reports: Dict[Tuple[str, str], QuantUpstreamReport] = {}
        self._claims: Dict[Tuple[str, str], InternalClaim] = {}
        self._responses: Dict[Tuple[str, str], InternalResponse] = {}

    def _accepted(self, report: QuantUpstreamReport, receipt_id: str,
                  now: datetime) -> UpstreamReceipt:
        """Check exact report bytes against an independently retrieved live receipt."""
        _utc(now, "NOW")
        if type(report) is not QuantUpstreamReport:
            raise InternalFanoutError("REPORT_INVALID")
        _label(receipt_id, "RECEIPT_ID")
        try:
            receipt = self._acceptance_lookup(receipt_id)
            active = self._acceptance_is_active(receipt_id)
        except Exception as exc:
            raise InternalFanoutError("UPSTREAM_NOT_ACTIVE") from exc
        if (type(receipt) is not UpstreamReceipt or active is not True
                or receipt.receipt_id != receipt_id
                or receipt.report_id != report.report_id
                or receipt.envelope_hash != report.envelope_hash
                or receipt.sender_agent_id != report.sender_agent_id
                or receipt.ack.receiver_agent_id != report.receiver_agent_id
                or receipt.ack.report_id != report.report_id
                or receipt.ack.verification_source != "upstream-inbox-v1"):
            raise InternalFanoutError("UPSTREAM_NOT_ACTIVE")
        if (report.created_at > receipt.accepted_at or receipt.accepted_at > now
                or receipt.ack.acked_at > now):
            raise InternalFanoutError("ACCEPTANCE_TIME_INVALID")
        if report.report_kind != "ANOMALY":
            raise InternalFanoutError("BUSINESS_SIGNAL_REQUIRED")
        return receipt

    def _signal(self, signal_id: str, report: QuantUpstreamReport,
                now: datetime) -> SignalPoolEntry:
        """Bind an independently read signal ID to the report's evidence event."""
        _label(signal_id, "SIGNAL_ID")
        _utc(now, "NOW")
        try:
            entry = self._signal_lookup(signal_id, now)
        except Exception as exc:
            raise InternalFanoutError("SIGNAL_BINDING_INVALID") from exc
        if (type(entry) is not SignalPoolEntry
                or entry.signal_id != signal_id
                or entry.status == STATUS_REVOKED
                or entry.producer_agent_id != report.sender_agent_id
                or entry.report_kind != report.report_kind
                or entry.event_id != report.event_id
                or entry.evidence_hash != report.evidence_hash
                or entry.evidence_version != report.evidence_version
                or entry.created_at > now):
            raise InternalFanoutError("SIGNAL_BINDING_INVALID")
        return entry

    def _authorized(self, team: str, identity: str, now: datetime) -> None:
        _utc(now, "NOW")
        if team not in _TEAMS:
            raise InternalFanoutError("TEAM_INVALID")
        _label(identity, "CONSUMER_IDENTITY")
        try:
            authorized = self._consumer_authorized(team, identity, now)
        except Exception as exc:
            raise InternalFanoutError("CONSUMER_NOT_AUTHORIZED") from exc
        if authorized is not True:
            raise InternalFanoutError("CONSUMER_NOT_AUTHORIZED")

    def _current(self, signal_id: str, team: str, now: datetime,
                 identity: str) -> Tuple[Tuple[str, str], InternalSubscription]:
        _label(signal_id, "SIGNAL_ID")
        self._authorized(team, identity, now)
        key = (signal_id, team)
        subscription = self._subscriptions.get(key)
        if subscription is None:
            raise InternalFanoutError("SUBSCRIPTION_UNKNOWN")
        if subscription.consumer_identity != identity:
            raise InternalFanoutError("CONSUMER_MISMATCH")
        if now < subscription.planned_at:
            raise InternalFanoutError("SUBSCRIPTION_NOT_VISIBLE")
        self._accepted(self._reports[key], subscription.receipt_id, now)
        self._signal(signal_id, self._reports[key], now)
        return key, subscription

    def plan(self, *, report: QuantUpstreamReport, receipt_id: str,
             signal_id: str, team: str, consumer_identity: str,
             now: datetime) -> InternalSubscription:
        """Plan one team without constructing any external dispatch intent."""
        _label(signal_id, "SIGNAL_ID")
        self._authorized(team, consumer_identity, now)
        receipt = self._accepted(report, receipt_id, now)
        self._signal(signal_id, report, now)
        key = (signal_id, team)
        previous = self._subscriptions.get(key)
        if previous is not None:
            if now < previous.planned_at:
                raise InternalFanoutError("SUBSCRIPTION_NOT_VISIBLE")
            if (previous.report_id != report.report_id
                    or previous.receipt_id != receipt_id
                    or previous.consumer_identity != consumer_identity
                    or previous.route != _ROUTES[team]):
                raise InternalFanoutError("SUBSCRIPTION_CONFLICT")
            return previous
        subscription = InternalSubscription(
            signal_id=signal_id, report_id=report.report_id,
            receipt_id=receipt.receipt_id, team=team, route=_ROUTES[team],
            consumer_identity=consumer_identity, planned_at=now,
            subscription_id=digest(["internal-subscription-v1", signal_id,
                                    team, receipt_id, consumer_identity]))
        self._subscriptions[key] = subscription
        self._reports[key] = report
        return subscription

    def fanout(self, *, report: QuantUpstreamReport, receipt_id: str,
               signal_id: str, research_identity: str, trading_identity: str,
               now: datetime) -> FanoutResult:
        """Plan each branch independently; return explicit per-team failures."""
        subscriptions: Dict[str, InternalSubscription] = {}
        errors = []
        for team, identity in ((RESEARCH, research_identity),
                               (TRADING, trading_identity)):
            try:
                subscriptions[team] = self.plan(
                    report=report, receipt_id=receipt_id, signal_id=signal_id,
                    team=team, consumer_identity=identity, now=now)
            except (InternalFanoutError, ValueError) as exc:
                errors.append((team, str(exc)))
        return FanoutResult(research=subscriptions.get(RESEARCH),
                            trading=subscriptions.get(TRADING),
                            errors=tuple(errors))

    def claim(self, *, signal_id: str, team: str, consumer_identity: str,
              now: datetime, lease_until: datetime) -> InternalClaim:
        """Recheck live acceptance and permissions before claiming a branch."""
        key, subscription = self._current(signal_id, team, now, consumer_identity)
        _utc(lease_until, "LEASE_UNTIL")
        if now < subscription.planned_at or lease_until <= now:
            raise InternalFanoutError("CLAIM_WINDOW_INVALID")
        if key in self._claims or key in self._responses:
            raise InternalFanoutError("SUBSCRIPTION_NOT_CLAIMABLE")
        claim = InternalClaim(
            subscription_id=subscription.subscription_id,
            consumer_identity=consumer_identity, claimed_at=now,
            lease_until=lease_until,
            claim_id=digest(["internal-claim-v1", subscription.subscription_id,
                             now.isoformat(), lease_until.isoformat()]))
        self._claims[key] = claim
        return claim

    def finish(self, *, signal_id: str, team: str, consumer_identity: str,
               claim: InternalClaim, outcome: str, now: datetime,
               reply: Optional[str] = None) -> InternalResponse:
        """Settle a local claim, never producing a delivery ACK or order."""
        key, subscription = self._current(signal_id, team, now, consumer_identity)
        if type(claim) is not InternalClaim or self._claims.get(key) != claim:
            raise InternalFanoutError("CLAIM_NOT_CURRENT")
        if now < claim.claimed_at:
            raise InternalFanoutError("CLAIM_NOT_VISIBLE")
        if claim.subscription_id != subscription.subscription_id or claim.consumer_identity != consumer_identity:
            raise InternalFanoutError("CLAIM_MISMATCH")
        if type(outcome) is not str or outcome not in (LOGICAL_RECEIVED, FAILED, UNKNOWN):
            raise InternalFanoutError("OUTCOME_INVALID")
        if (type(reply) is not str and reply is not None) or (
                outcome != LOGICAL_RECEIVED and reply is not None) or (
                reply is not None and reply not in _REPLIES[team]):
            raise InternalFanoutError("REPLY_INVALID")
        if key in self._responses:
            prior = self._responses[key]
            if now < prior.recorded_at:
                raise InternalFanoutError("RESPONSE_NOT_VISIBLE")
            if prior.outcome == outcome and prior.reply == reply:
                return prior
            raise InternalFanoutError("RESPONSE_CONFLICT")
        if now < claim.claimed_at or now >= claim.lease_until:
            raise InternalFanoutError("CLAIM_EXPIRED")
        response = InternalResponse(
            subscription_id=subscription.subscription_id, team=team,
            consumer_identity=consumer_identity, outcome=outcome,
            recorded_at=now, reply=reply)
        self._responses[key] = response
        return response

    def consume(self, *, signal_id: str, team: str,
                consumer_identity: str, now: datetime) -> Optional[InternalResponse]:
        """Read a response only after rechecking live acceptance and identity."""
        key, _ = self._current(signal_id, team, now, consumer_identity)
        response = self._responses.get(key)
        return response if response is not None and response.recorded_at <= now else None

    def status(self, *, signal_id: str, team: str,
               consumer_identity: str, now: datetime) -> str:
        """Return REVOKED if upstream is no longer live; never mask it as delivered."""
        _label(signal_id, "SIGNAL_ID")
        self._authorized(team, consumer_identity, now)
        key = (signal_id, team)
        subscription = self._subscriptions.get(key)
        if subscription is None:
            raise InternalFanoutError("SUBSCRIPTION_UNKNOWN")
        if subscription.consumer_identity != consumer_identity:
            raise InternalFanoutError("CONSUMER_MISMATCH")
        if now < subscription.planned_at:
            raise InternalFanoutError("SUBSCRIPTION_NOT_VISIBLE")
        try:
            self._accepted(self._reports[key], subscription.receipt_id, now)
            self._signal(signal_id, self._reports[key], now)
        except InternalFanoutError:
            return "REVOKED_OR_INACTIVE"
        response = self._responses.get(key)
        if response is not None and response.recorded_at <= now:
            return response.outcome
        claim = self._claims.get(key)
        return CLAIMED if claim is not None and claim.claimed_at <= now else PLANNED
