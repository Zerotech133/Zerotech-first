"""Offline/test-only composition of outbox evidence with a trusted sector inbox.

The injected inbox must be the independently owned, authoritative inbox, not a
caller-created mirror. This adapter only reads in-memory ledgers and grants no
production, notification, dispatch, or trading permission. In particular,
SignalPool.mark_accepted accepts arbitrary ReceiptBindingPort implementations;
this module cannot make that production entry point trustworthy by itself.
"""
from __future__ import annotations

from datetime import datetime

from quant_backtest.agent_bridge.mapping import OutboxBindingAdapter, to_sector_report
from quant_backtest.quant_sector.upstream_inbox import (
    QuantUpstreamReport,
    UpstreamInbox,
    UpstreamReceipt,
)
from quant_backtest.watch_dispatch.agent_reporting import AgentReport
from quant_backtest.watch_dispatch.contracts import EvidenceEnvelope, RouteIntent
from quant_backtest.watch_dispatch.route_outbox import (
    ReportFingerprint,
    RouteAttempt,
    RouteOutbox,
    RouteReceipt,
)
from quant_backtest.watch_dispatch.canonical import utc_text
from quant_backtest.watch_dispatch.signal_pool import AttemptBinding, ReceiptBindingPort

ROUTE = "QUANT_UPSTREAM_REPORT"


class TrustedAcceptanceBindingAdapter(ReceiptBindingPort):
    """Read-only exact-attempt binding against an injected authoritative inbox.

    Neither an ACCEPTED_VERIFIED outbox status nor a caller-supplied ACK alone
    proves sector acceptance. Both independent ledgers and their epochs,
    identities, contents and observed timestamps must agree at lookup time.
    Private outbox indexes are read only because no public exact-attempt API
    exists. No timestamps are generated or substituted here.
    """

    def __init__(self, outbox: RouteOutbox, envelope: EvidenceEnvelope,
                 report: AgentReport, inbox: UpstreamInbox, *, now: datetime) -> None:
        if (type(outbox) is not RouteOutbox or type(envelope) is not EvidenceEnvelope
                or type(report) is not AgentReport or type(inbox) is not UpstreamInbox):
            raise ValueError("TRUSTED_ACCEPTANCE_INPUT_INVALID")
        utc_text(now)
        self._now = now
        self._outbox = outbox
        self._envelope = envelope
        self._report = report
        self._inbox = inbox
        self._outbox_binding = OutboxBindingAdapter(outbox, envelope)

    @property
    def production_permitted(self) -> bool:
        """Explicitly prohibit treating this in-memory adapter as production."""
        return False

    @property
    def notification_permitted(self) -> bool:
        """This adapter is not a notification authority."""
        return False

    @property
    def trading_permitted(self) -> bool:
        """This adapter is not a trading authority."""
        return False

    def binding_for(self, attempt_id: str) -> AttemptBinding | None:
        """Return a binding only when both live ledgers prove this exact attempt.

        Any malformed, stale, contradictory or unavailable evidence fails
        closed. This is deliberately not an ACK verifier or network receiver.
        """
        if type(attempt_id) is not str or not attempt_id:
            return None
        try:
            return self._binding_for(attempt_id)
        except Exception:
            # An unavailable or failing injected trust root must never be
            # converted into an acceptance binding.
            return None

    def _binding_for(self, attempt_id: str) -> AttemptBinding | None:
        binding = self._outbox_binding.binding_for(attempt_id)
        if type(binding) is not AttemptBinding or binding.route != "QUANT_UPSTREAM_REPORT":
            return None
        report = self._report
        envelope = self._envelope
        if (not report.binds(envelope) or report.sender.role != "WATCH"
                or report.receiver.role != "QUANT_SECTOR"):
            return None
        attempt = self._outbox._attempts.get(attempt_id)
        if type(attempt) is not RouteAttempt or type(attempt.receipt) is not RouteReceipt:
            return None
        route_receipt = attempt.receipt
        key = attempt.idempotency_key
        current = self._outbox._intents.get(key)
        if type(current) is not RouteIntent:
            return None
        intent = next(
            (known for known in (current, *self._outbox._intent_history.get(key, ()))
             if type(known) is RouteIntent and known.intent_id == attempt.intent_id),
            None)
        if intent is None:
            return None
        fingerprint = ReportFingerprint(
            report_id=report.report_id, report_kind=report.report_kind,
            event_id=report.event_id, evidence_version=report.evidence_version,
            envelope_hash=report.envelope_hash,
            sender_agent_id=report.sender.agent_id,
            receiver_agent_id=report.receiver.agent_id,
            created_at=report.created_at)
        original = self._outbox._report_fingerprints.get(key)
        if type(original) is not ReportFingerprint or original != fingerprint:
            return None
        mapped = QuantUpstreamReport(
            report_id=original.report_id, report_kind=original.report_kind,
            event_id=original.event_id, evidence_hash=original.envelope_hash,
            evidence_version=original.evidence_version,
            sender_agent_id=original.sender_agent_id,
            receiver_agent_id=original.receiver_agent_id,
            created_at=original.created_at)
        if mapped != to_sector_report(report):
            return None
        if (self._outbox._origin_created_at.get(key) != original.created_at
                or key in self._outbox._revoked
                or self._outbox._key_for(
                    envelope, "QUANT_UPSTREAM_REPORT", "AGENT",
                    report.receiver.agent_id) != key
                or intent.target_type != "AGENT"
                or intent.target_id != report.receiver.agent_id
                or intent.payload_digest != envelope.envelope_hash
                or intent.control_epoch < self._outbox.epoch_floor(envelope)
                or current.control_epoch != intent.control_epoch
                or route_receipt.attempt_id != attempt_id
                or route_receipt.intent_id != intent.intent_id
                or route_receipt.route != ROUTE):
            return None
        receipt = self._inbox.receipt_for_report(report.report_id)
        if (type(receipt) is not UpstreamReceipt
                or not self._inbox.is_active(receipt.receipt_id)
                or self._inbox.is_revoked(report.report_id)
                or self._inbox.get(receipt.receipt_id) is not receipt
                or self._inbox._reports.get(report.report_id) != mapped):
            return None
        ack = receipt.ack
        if (receipt.report_id != report.report_id
                or receipt.envelope_hash != mapped.envelope_hash
                or receipt.sender_agent_id != report.sender.agent_id
                or receipt.ledger_epoch != self._inbox.ledger_epoch
                or intent.control_epoch != receipt.ledger_epoch
                or ack.report_id != report.report_id
                or ack.receiver_agent_id != report.receiver.agent_id
                or ack.ack_id != binding.external_message_id
                or ack.ack_id != route_receipt.external_message_id
                or ack.verification_source != route_receipt.verification_source
                or ack.acked_at != route_receipt.observed_at):
            return None
        if not (envelope.freeze_at <= report.created_at == fingerprint.created_at
                <= intent.created_at <= attempt.claim_at < attempt.lease_until
                and report.created_at <= receipt.accepted_at <= ack.acked_at
                == route_receipt.observed_at <= self._now
                and report.created_at < envelope.valid_until
                and ack.acked_at < envelope.valid_until
                and report.sender.registered_at <= report.created_at
                and report.receiver.registered_at <= report.created_at
                and ack.acked_at < report.sender.valid_until
                and ack.acked_at < report.receiver.valid_until):
            return None
        return binding
