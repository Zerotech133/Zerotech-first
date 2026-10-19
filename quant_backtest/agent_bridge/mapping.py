"""Pure report translation and read-only proof of outbox attempt binding.

This is a composition root: neither core domain imports this package. The
watch-side AgentReport.envelope_hash is the *evidence envelope* hash and maps
to QuantUpstreamReport.evidence_hash. The sector report's envelope_hash is a
*different, derived payload hash*, including created_at and both agent IDs.
Never normalize or rewrite mapped values: this would drift the inbox key.
"""
from __future__ import annotations

from typing import Mapping

from quant_backtest.quant_sector.upstream_inbox import QuantUpstreamReport, UpstreamAck
from quant_backtest.watch_dispatch.agent_reporting import AgentAck, AgentReport
from quant_backtest.watch_dispatch.contracts import EvidenceEnvelope, RouteIntent
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
    """Read-only, exact attempt proof from the outbox ledger.

    RouteOutbox has no public lookup by attempt_id: get_attempts needs a
    target_id, which the pool receipt does not contain. This temporary
    composition-root adapter reads existing private ledgers without mutation;
    replace these accesses when outbox gains a public exact-attempt lookup.
    The outbox receipt is the only accepted proof, not an inbox report ID.
    """

    def __init__(self, outbox: RouteOutbox, envelope: EvidenceEnvelope) -> None:
        self._outbox = outbox
        self._envelope = envelope

    def binding_for(self, attempt_id: str) -> AttemptBinding | None:
        """Return a settled attempt belonging to this exact envelope."""
        attempt = self._outbox._attempts.get(attempt_id)
        if attempt is None or attempt.attempt_id != attempt_id:
            return None
        key = attempt.idempotency_key
        current = self._outbox._intents.get(key)
        receipt = attempt.receipt
        if (current is None or receipt is None
                or attempt.outcome != ACCEPTED_VERIFIED
                or receipt.attempt_id != attempt_id
                or receipt.intent_id != attempt.intent_id
                or receipt.route != attempt.route
                or not receipt.external_message_id
                or not receipt.verification_source
                or key in self._outbox._revoked):
            return None
        # Only current or append-only historical intent is admissible; never
        # infer an unknown attempt's identity from the injected envelope.
        intent = next(
            (known for known in (current, *self._outbox._intent_history.get(key, ()))
             if known.intent_id == attempt.intent_id), None)
        if (type(intent) is not RouteIntent
                or self._outbox._key_for(
                    self._envelope, intent.route, intent.target_type, intent.target_id) != key
                or intent.route != attempt.route
                or (intent.event_id, intent.envelope_hash,
                    intent.evidence_version) != (
                        self._envelope.event_id, self._envelope.envelope_hash,
                        self._envelope.evidence_version)
                or intent.tenant_scope != self._envelope.tenant_or_owner_id
                or self._outbox._identities.get(key) != self._outbox._identity(self._envelope)
                or intent.control_epoch < self._outbox.epoch_floor(self._envelope)):
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
