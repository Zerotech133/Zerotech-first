"""Read-only, live sector-inbox authority for an original watch report ACK.

This adapter is injected when constructing the outbox, never supplied by a
finish caller. It proves the current upstream ledger entry rather than trusting
matching ACK fields or a watch evidence hash as an upstream payload hash.
"""
from __future__ import annotations

from datetime import datetime

from quant_backtest.quant_sector.upstream_inbox import QuantUpstreamReport, UpstreamInbox
from quant_backtest.watch_dispatch.route_outbox import ReportFingerprint


class UpstreamAckAuthority:
    """Check one immutable report against a currently active sector receipt."""

    def __init__(self, inbox: UpstreamInbox) -> None:
        self._inbox = inbox

    def active_ack(self, fingerprint: ReportFingerprint, ack_id: str,
                   verification_source: str, acked_at: datetime, *,
                   now: datetime) -> bool:
        """Return true only for the exact live receipt and original report."""
        try:
            if (type(fingerprint) is not ReportFingerprint
                    or type(ack_id) is not str or not ack_id
                    or type(verification_source) is not str or not verification_source):
                return False
            report = QuantUpstreamReport(
                report_id=fingerprint.report_id,
                report_kind=fingerprint.report_kind,
                event_id=fingerprint.event_id,
                evidence_hash=fingerprint.envelope_hash,
                evidence_version=fingerprint.evidence_version,
                sender_agent_id=fingerprint.sender_agent_id,
                receiver_agent_id=fingerprint.receiver_agent_id,
                created_at=fingerprint.created_at)
            receipt = self._inbox.receipt_for_report(report.report_id)
            if (receipt is None or self._inbox.is_revoked(report.report_id)
                    or not self._inbox.is_active(receipt.receipt_id)
                    or self._inbox.get(receipt.receipt_id) != receipt):
                return False
            return (receipt.report_id == report.report_id
                    and receipt.envelope_hash == report.envelope_hash
                    and receipt.sender_agent_id == report.sender_agent_id
                    and receipt.accepted_at >= report.created_at
                    and receipt.accepted_at <= acked_at <= now
                    and receipt.ack.ack_id == ack_id
                    and receipt.ack.report_id == report.report_id
                    and receipt.ack.receiver_agent_id == report.receiver_agent_id
                    and receipt.ack.acked_at == acked_at
                    and receipt.ack.verification_source == verification_source)
        except (AttributeError, TypeError, ValueError):
            return False
