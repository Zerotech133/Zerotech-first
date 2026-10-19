"""Agent-to-agent upstream reporting. Watch reports to the quant-sector Agent.

Watch is a sub-module of quant trading: every anomaly (ANOMALY) and every
problem or health fault (HEALTH) is reported upward to the quant sector's
registered receiving Agent — never directly to Feishu, research, or a desk.
Sender and receiver are bound Agent identities resolved through an injected
directory port; only an independently verified agent-level ACK completes a
report. Nothing here is executable, persistent, or production-permitted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from quant_backtest.watch_dispatch.canonical import utc_text
from quant_backtest.watch_dispatch.contracts import EvidenceEnvelope
from quant_backtest.watch_dispatch.route_outbox import (
    ACCEPTED_VERIFIED, DEFINITELY_REJECTED, ReportFingerprint, RouteAttempt,
    RouteOutbox, UNKNOWN,
)

_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_ROLES = frozenset(("WATCH", "QUANT_SECTOR"))
_KINDS = frozenset(("ANOMALY", "HEALTH"))


class AgentReportingError(ValueError):
    """Fail-closed agent reporting violation."""


def _label(value: object, field: str) -> None:
    if type(value) is not str or _LABEL.fullmatch(value) is None:
        raise AgentReportingError(field + "_INVALID")


def _utc(value: object, field: str) -> None:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise AgentReportingError(field + "_INVALID")
    utc_text(value)  # type: ignore[arg-type]


@dataclass(frozen=True)
class AgentIdentity:
    """One registered agent; authority comes only from the directory port."""

    agent_id: str
    role: str
    registry_ref: str
    registered_at: datetime
    valid_until: datetime

    def __post_init__(self) -> None:
        _label(self.agent_id, "AGENT_ID")
        if self.role not in _ROLES or type(self.role) is not str:
            raise AgentReportingError("AGENT_ROLE_INVALID")
        _label(self.registry_ref, "REGISTRY_REF")
        _utc(self.registered_at, "REGISTERED_AT")
        _utc(self.valid_until, "AGENT_VALID_UNTIL")
        if not (self.registered_at < self.valid_until):
            raise AgentReportingError("AGENT_TIME_ORDER_INVALID")

    def current(self, now: datetime) -> bool:
        return self.valid_until > now


@dataclass(frozen=True)
class AgentReport:
    """Immutable record of one upward agent report; never executable."""

    report_id: str
    report_kind: str
    event_id: str
    evidence_version: int
    envelope_hash: str
    sender: AgentIdentity
    receiver: AgentIdentity
    created_at: datetime
    non_executable: bool = True

    @property
    def dispatch_permitted(self) -> bool:
        return False

    @property
    def notification_permitted(self) -> bool:
        return False

    @property
    def trading_permitted(self) -> bool:
        return False

    def __post_init__(self) -> None:
        if self.non_executable is not True:
            raise AgentReportingError("EXECUTION_FORBIDDEN")
        _label(self.report_id, "REPORT_ID")
        if self.report_kind not in _KINDS or type(self.report_kind) is not str:
            raise AgentReportingError("REPORT_KIND_INVALID")
        _label(self.event_id, "EVENT_ID")
        if type(self.evidence_version) is not int or self.evidence_version < 1:
            raise AgentReportingError("VERSION_INVALID")
        if type(self.envelope_hash) is not str or len(self.envelope_hash) != 64:
            raise AgentReportingError("ENVELOPE_HASH_INVALID")
        if type(self.sender) is not AgentIdentity or type(self.receiver) is not AgentIdentity:
            raise AgentReportingError("AGENT_IDENTITY_INVALID")
        if self.sender.agent_id == self.receiver.agent_id:
            raise AgentReportingError("AGENT_SELF_REPORT_FORBIDDEN")
        _utc(self.created_at, "REPORT_CREATED_AT")

    def binds(self, envelope: EvidenceEnvelope) -> bool:
        return (self.event_id == envelope.event_id
                and self.evidence_version == envelope.evidence_version
                and self.envelope_hash == envelope.envelope_hash)


@dataclass(frozen=True)
class AgentAck:
    """Agent-level acknowledgement; trusted only after the ack port verifies."""

    ack_id: str
    report_id: str
    receiver_agent_id: str
    acked_at: datetime
    verification_source: str

    def __post_init__(self) -> None:
        for name in ("ack_id", "report_id", "receiver_agent_id", "verification_source"):
            _label(getattr(self, name), name.upper())
        _utc(self.acked_at, "ACKED_AT")


class AgentDirectoryPort(Protocol):
    """Independent agent registry; may never derive from caller-supplied data."""

    def resolve(self, agent_id: str, *, role: str, now: datetime) -> AgentIdentity | None: ...


class AgentAckPort(Protocol):
    """Independent verifier that this ack exists and binds this exact report."""

    def verify(self, ack: AgentAck, report: AgentReport, now: datetime) -> AgentAck | None: ...


@dataclass(frozen=True)
class AgentRejectionEvidence:
    """Independent evidence that an upstream report was definitely rejected."""

    rejection_ref: str
    verification_source: str
    observed_at: datetime


class AgentRejectionPort(Protocol):
    """Verify an upstream rejection independently of the acknowledgement port."""

    def verify_rejection(self, report: AgentReport, *,
                         now: datetime) -> AgentRejectionEvidence | None: ...


class AgentUpstreamReporter:
    """Plans QUANT_UPSTREAM_REPORT intents bound to the receiving agent."""

    def __init__(self, sender: AgentIdentity, directory: AgentDirectoryPort,
                 ack_port: AgentAckPort,
                 rejection_port: AgentRejectionPort | None = None) -> None:
        if type(sender) is not AgentIdentity or sender.role != "WATCH":
            raise AgentReportingError("SENDER_MUST_BE_WATCH_AGENT")
        self._sender = sender
        self._directory = directory
        self._ack_port = ack_port
        self._rejection_port = rejection_port

    @property
    def dispatch_permitted(self) -> bool:
        return False

    @property
    def notification_permitted(self) -> bool:
        return False

    @property
    def trading_permitted(self) -> bool:
        return False

    def report(self, envelope: EvidenceEnvelope, outbox: RouteOutbox, *,
               report_kind: str, receiver_agent_id: str, report_id: str,
               control_epoch: int, now: datetime) -> AgentReport:
        _utc(now, "REPORT_NOW")
        envelope.validate(now=now)
        if report_kind not in _KINDS or type(report_kind) is not str:
            raise AgentReportingError("REPORT_KIND_INVALID")
        _label(receiver_agent_id, "RECEIVER_AGENT_ID")
        _label(report_id, "REPORT_ID")
        if now >= self._sender.valid_until:
            raise AgentReportingError("SENDER_NOT_CURRENT")
        receiver = self._directory.resolve(receiver_agent_id, role="QUANT_SECTOR", now=now)
        if receiver is None:
            raise AgentReportingError("AGENT_UNRESOLVED")
        if not receiver.current(now):
            raise AgentReportingError("AGENT_NOT_CURRENT")
        if self._sender.agent_id == receiver.agent_id:
            raise AgentReportingError("AGENT_SELF_REPORT_FORBIDDEN")
        fingerprint = ReportFingerprint(
            report_id=report_id, report_kind=report_kind,
            event_id=envelope.event_id, evidence_version=envelope.evidence_version,
            envelope_hash=envelope.envelope_hash, sender_agent_id=self._sender.agent_id,
            receiver_agent_id=receiver.agent_id, created_at=now)
        outbox.plan(envelope, route="QUANT_UPSTREAM_REPORT", target_type="AGENT",
                    target_id=receiver.agent_id, policy_version="agent-report-v1",
                    control_epoch=control_epoch, intent_id=f"report-{report_id}",
                    created_at=now, valid_until=envelope.valid_until,
                    report_fingerprint=fingerprint)
        return AgentReport(
            report_id=report_id, report_kind=report_kind,
            event_id=envelope.event_id,
            evidence_version=envelope.evidence_version,
            envelope_hash=envelope.envelope_hash, sender=self._sender,
            receiver=receiver, created_at=now)

    def retry_report(self, envelope: EvidenceEnvelope, outbox: RouteOutbox,
                     prior: AgentReport, *, generation: int,
                     control_epoch: int, now: datetime) -> AgentReport:
        """Supersede an UNKNOWN report with its next audited generation."""
        if type(generation) is not int or generation < 1:
            raise AgentReportingError("RETRY_NOT_PERMITTED")
        attempts = outbox.get_attempts(
            envelope, route="QUANT_UPSTREAM_REPORT", target_type="AGENT",
            target_id=prior.receiver.agent_id)
        current = outbox.get_intent(
            envelope, route="QUANT_UPSTREAM_REPORT", target_type="AGENT",
            target_id=prior.receiver.agent_id)
        prior_intent_id = (f"report-{prior.report_id}" if generation == 1
                           else f"report-{prior.report_id}-g{generation - 1}")
        key = outbox._key_for(
            envelope, "QUANT_UPSTREAM_REPORT", "AGENT", prior.receiver.agent_id)
        fingerprint = ReportFingerprint(
            report_id=prior.report_id, report_kind=prior.report_kind,
            event_id=prior.event_id, evidence_version=prior.evidence_version,
            envelope_hash=prior.envelope_hash, sender_agent_id=prior.sender.agent_id,
            receiver_agent_id=prior.receiver.agent_id, created_at=prior.created_at)
        if (not prior.binds(envelope) or prior.sender != self._sender
                or key in outbox._revoked
                or not attempts or attempts[-1].outcome != UNKNOWN
                or current is None or current.status != UNKNOWN
                or current.intent_id != prior_intent_id
                or outbox._report_fingerprints.get(key) != fingerprint):
            raise AgentReportingError("RETRY_NOT_PERMITTED")
        _utc(now, "REPORT_NOW")
        if now >= self._sender.valid_until:
            raise AgentReportingError("SENDER_NOT_CURRENT")
        receiver = self._directory.resolve(
            prior.receiver.agent_id, role="QUANT_SECTOR", now=now)
        if receiver is None or receiver != prior.receiver or not receiver.current(now):
            raise AgentReportingError("AGENT_NOT_CURRENT")
        outbox.plan(
            envelope, route="QUANT_UPSTREAM_REPORT", target_type="AGENT",
            target_id=prior.receiver.agent_id, policy_version="agent-report-v1",
            control_epoch=control_epoch,
            intent_id=f"report-{prior.report_id}-g{generation}",
            created_at=now, valid_until=envelope.valid_until,
            generation=generation, report_fingerprint=fingerprint)
        return prior

    def claim_and_confirm(self, envelope: EvidenceEnvelope, outbox: RouteOutbox,
                          report: AgentReport, *, claim_token: str,
                          claim_at: datetime, lease_until: datetime,
                          ack: AgentAck, now: datetime) -> tuple[RouteAttempt, RouteAttempt | None]:
        """Claim the upstream intent, then finish only on a port-verified ack."""
        if type(report) is not AgentReport or not report.binds(envelope):
            raise AgentReportingError("REPORT_IDENTITY_MISMATCH")
        if report.sender != self._sender:
            raise AgentReportingError("REPORT_SENDER_MISMATCH")
        key = outbox._key_for(
            envelope, "QUANT_UPSTREAM_REPORT", "AGENT", report.receiver.agent_id)
        fingerprint = ReportFingerprint(
            report_id=report.report_id, report_kind=report.report_kind,
            event_id=report.event_id, evidence_version=report.evidence_version,
            envelope_hash=report.envelope_hash,
            sender_agent_id=report.sender.agent_id,
            receiver_agent_id=report.receiver.agent_id, created_at=report.created_at)
        intent = outbox.get_intent(
            envelope, route="QUANT_UPSTREAM_REPORT", target_type="AGENT",
            target_id=report.receiver.agent_id)
        generation = outbox._generations.get(key)
        expected_id = (f"report-{report.report_id}" if generation == 0 else
                       f"report-{report.report_id}-g{generation}")
        if (outbox._report_fingerprints.get(key) != fingerprint
                or intent is None or type(generation) is not int
                or generation < 0 or intent.intent_id != expected_id):
            raise AgentReportingError("REPORT_IDENTITY_MISMATCH")
        # The fingerprint records agent IDs, not their entire registry identities.
        # Recheck both immutable identities and their validity before consuming a
        # shared attempt, including the original report and current claim times.
        receiver = self._directory.resolve(
            report.receiver.agent_id, role="QUANT_SECTOR", now=now)
        if (receiver != report.receiver or report.sender.role != "WATCH"
                or report.receiver.role != "QUANT_SECTOR"
                or any(not (identity.registered_at <= moment < identity.valid_until)
                       for identity in (report.sender, report.receiver)
                       for moment in (report.created_at, claim_at, now))):
            raise AgentReportingError("AGENT_NOT_CURRENT")
        attempt = outbox.claim(envelope, route="QUANT_UPSTREAM_REPORT",
                               target_type="AGENT", target_id=report.receiver.agent_id,
                               claim_token=claim_token, claim_at=claim_at,
                               lease_until=lease_until)
        try:
            verified = self._ack_port.verify(ack, report, now)
        except Exception:
            verified = None
        # Verification is a necessary report binding, not proof that a sector
        # receipt remains active. The outbox checks live authority for a prior
        # generation's ACK before accepting its original timestamp.
        if (type(ack) is not AgentAck or type(verified) is not AgentAck
                or verified != ack or verified.report_id != report.report_id
                or verified.receiver_agent_id != report.receiver.agent_id
                or verified.acked_at < report.created_at
                or verified.acked_at > now):
            verified = None
        if verified is not None:
            finished = outbox.finish(
                attempt.attempt_id, outcome=ACCEPTED_VERIFIED, now=now,
                external_message_id=verified.ack_id,
                verification_source=verified.verification_source,
                observed_at=verified.acked_at)
            return attempt, finished
        if self._rejection_port is not None:
            rejection = self._rejection_port.verify_rejection(report, now=now)
            if rejection is not None:
                if (type(rejection) is not AgentRejectionEvidence
                        or type(rejection.rejection_ref) is not str
                        or not rejection.rejection_ref
                        or type(rejection.verification_source) is not str
                        or not rejection.verification_source):
                    raise AgentReportingError("REJECTION_EVIDENCE_INVALID")
                try:
                    _utc(rejection.observed_at, "REJECTION_EVIDENCE")
                except (AgentReportingError, ValueError) as exc:
                    raise AgentReportingError("REJECTION_EVIDENCE_INVALID") from exc
                finished = outbox.finish(
                    attempt.attempt_id, outcome=DEFINITELY_REJECTED,
                    now=now, external_message_id=rejection.rejection_ref,
                    verification_source=rejection.verification_source,
                    observed_at=rejection.observed_at)
                return attempt, finished
        finished = outbox.finish(attempt.attempt_id, outcome=UNKNOWN, now=now)
        return attempt, finished
