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
    ACCEPTED_VERIFIED, RouteAttempt, RouteOutbox, UNKNOWN,
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


class AgentUpstreamReporter:
    """Plans a single QUANT_UPSTREAM_REPORT intent bound to the receiving agent."""

    def __init__(self, sender: AgentIdentity, directory: AgentDirectoryPort,
                 ack_port: AgentAckPort) -> None:
        if type(sender) is not AgentIdentity or sender.role != "WATCH":
            raise AgentReportingError("SENDER_MUST_BE_WATCH_AGENT")
        self._sender = sender
        self._directory = directory
        self._ack_port = ack_port

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
               now: datetime) -> AgentReport:
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
        outbox.plan(envelope, route="QUANT_UPSTREAM_REPORT", target_type="AGENT",
                    target_id=receiver.agent_id, policy_version="agent-report-v1",
                    control_epoch=0, intent_id=f"report-{report_id}",
                    created_at=now, valid_until=envelope.valid_until)
        return AgentReport(
            report_id=report_id, report_kind=report_kind,
            event_id=envelope.event_id,
            evidence_version=envelope.evidence_version,
            envelope_hash=envelope.envelope_hash, sender=self._sender,
            receiver=receiver, created_at=now)

    def claim_and_confirm(self, envelope: EvidenceEnvelope, outbox: RouteOutbox,
                          report: AgentReport, *, claim_token: str,
                          claim_at: datetime, lease_until: datetime,
                          ack: AgentAck, now: datetime) -> tuple[RouteAttempt, RouteAttempt | None]:
        """Claim the upstream intent, then finish only on a port-verified ack."""
        if not report.binds(envelope):
            raise AgentReportingError("REPORT_IDENTITY_MISMATCH")
        if report.sender.agent_id != self._sender.agent_id:
            raise AgentReportingError("REPORT_SENDER_MISMATCH")
        attempt = outbox.claim(envelope, route="QUANT_UPSTREAM_REPORT",
                               target_type="AGENT", target_id=report.receiver.agent_id,
                               claim_token=claim_token, claim_at=claim_at,
                               lease_until=lease_until)
        verified = self._ack_port.verify(ack, report, now)
        if verified is None:
            finished = outbox.finish(attempt.attempt_id, outcome=UNKNOWN, now=now)
            return attempt, finished
        finished = outbox.finish(
            attempt.attempt_id, outcome=ACCEPTED_VERIFIED, now=now,
            external_message_id=verified.ack_id,
            verification_source=verified.verification_source,
            observed_at=verified.acked_at)
        return attempt, finished
