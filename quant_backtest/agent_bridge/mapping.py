"""Pure report translation and read-only proof of outbox attempt binding.

This is a composition root: neither core domain imports this package. The
watch-side AgentReport.envelope_hash is the *evidence envelope* hash and maps
to QuantUpstreamReport.evidence_hash. The sector report's envelope_hash is a
*different, derived payload hash*, including created_at and both agent IDs.
Never normalize or rewrite mapped values: this would drift the inbox key.
"""
from __future__ import annotations

from typing import Mapping

from quant_backtest.quant_sector.upstream_inbox import (
    QuantUpstreamReport,
    UpstreamAck,
    UpstreamInbox,
)
from quant_backtest.watch_dispatch.agent_reporting import AgentAck, AgentReport
from quant_backtest.watch_dispatch.contracts import EvidenceEnvelope
from quant_backtest.watch_dispatch.route_outbox import (
    ACCEPTED_VERIFIED,
    RouteOutbox,
)
from quant_backtest.watch_dispatch.signal_pool import AttemptBinding, ReceiptBindingPort

REPORT_FIELD_MAP: Mapping[str, str] = {
    "report_id": "report_id",
    "report_kind": "report_kind",
    "event_id": "event_id",
    "evidence_version": "evidence_version",
    "envelope_hash": "evidence_hash",  # Evidence hash, NOT sector derived envelope_hash.
    "sender.agent_id": "sender_agent_id",
    "receiver.agent_id": "receiver_agent_id",
    "created_at": "created_at",
}
ACK_FIELD_MAP: Mapping[str, str] = {
    "ack_id": "ack_id",
    "report_id": "report_id",
    "receiver_agent_id": "receiver_agent_id",
    "acked_at": "acked_at",
    "verification_source": "verification_source",
}


def to_sector_report(report: AgentReport) -> QuantUpstreamReport:
    """Copy each report field unchanged into the sector's strict local type."""
    if type(report) is not AgentReport:
        raise ValueError("REPORT_INVALID")
    return QuantUpstreamReport(
        **{target: getattr(report.sender, "agent_id") if source == "sender.agent_id"
           else getattr(report.receiver, "agent_id") if source == "receiver.agent_id"
           else getattr(report, source)
           for source, target in REPORT_FIELD_MAP.items()})


def to_agent_ack(ack: UpstreamAck) -> AgentAck:
    """Copy the sector-issued ACK unchanged into the watch's strict type."""
    if type(ack) is not UpstreamAck:
        raise ValueError("ACK_INVALID")
    return AgentAck(**{target: getattr(ack, source)
                       for source, target in ACK_FIELD_MAP.items()})


class OutboxBindingAdapter(ReceiptBindingPort):
    """Read-only attempt proof from the actual outbox, optionally cross-checking inbox.

    RouteOutbox currently has no public lookup by attempt_id: get_attempts
    needs the target_id, which a pool receipt does not contain. Hence the
    adapter reads existing private in-memory ledgers without mutating them.
    A future public exact-attempt lookup can replace only this implementation.
    """

    def __init__(self, outbox: RouteOutbox, envelope: EvidenceEnvelope,
                 inbox: UpstreamInbox | None = None) -> None:
        self._outbox = outbox
        self._envelope = envelope
        self._inbox = inbox

    def binding_for(self, attempt_id: str) -> AttemptBinding | None:
        """Return a settled, receipted attempt's actual immutable identity."""
        attempt = self._outbox._attempts.get(attempt_id)
        if attempt is None or attempt.attempt_id != attempt_id:
            return None
        intent = self._outbox._intents.get(attempt.idempotency_key)
        receipt = attempt.receipt
        if (intent is None or receipt is None
                or intent.intent_id != attempt.intent_id
                or attempt.outcome != ACCEPTED_VERIFIED
                or receipt.attempt_id != attempt_id
                or receipt.intent_id != intent.intent_id
                or receipt.route != attempt.route
                or intent.route != attempt.route
                or intent.envelope_hash != intent.payload_digest):
            return None
        # Envelope is construction context, not the source of the returned
        # identity: returning the attempt's *actual* identity lets the pool
        # reject cross-signal reuse as RECEIPT_IDENTITY_MISMATCH.
        if (self._inbox is not None
                and not self._inbox_receipt_matches(intent, receipt.external_message_id)):
            return None
        return AttemptBinding(
            attempt_id=attempt.attempt_id,
            external_message_id=receipt.external_message_id,
            outcome=attempt.outcome,
            route=attempt.route,
            event_id=intent.event_id,
            evidence_hash=intent.envelope_hash,
            evidence_version=intent.evidence_version,
        )

    def _inbox_receipt_matches(self, intent: object, external_ref: str) -> bool:
        """Independently confirm the ACK against an active inbox report."""
        assert self._inbox is not None
        for upstream_receipt in self._inbox.list_accepted():
            if upstream_receipt.ack.ack_id != external_ref:
                continue
            if not self._inbox.is_active(upstream_receipt.receipt_id):
                return False
            report = self._inbox._reports.get(upstream_receipt.report_id)
            if report is None:
                return False
            return (
                report.envelope_hash == upstream_receipt.envelope_hash
                and upstream_receipt.ack.report_id == report.report_id
                and upstream_receipt.sender_agent_id == report.sender_agent_id
                and report.event_id == intent.event_id
                and report.evidence_hash == intent.envelope_hash
                and report.evidence_version == intent.evidence_version
                and report.receiver_agent_id == intent.target_id
                and (intent.intent_id == f"report-{report.report_id}"
                     or (intent.intent_id.startswith(f"report-{report.report_id}-g")
                         and intent.intent_id[len(f"report-{report.report_id}-g"):].isdigit()))
            )
        return False
