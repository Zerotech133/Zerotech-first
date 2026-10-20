"""Offline, memory-only adapter from a synthetic signal to a quant inbox.

This module is not a production trust boundary. A caller supplies an entry,
report ID, receiving agent, inbox and observation time explicitly; it does not
verify a real pool, agent registry, evidence store or external recipient. The
ACK issued by UpstreamInbox is local bookkeeping, not an independently
verified delivery or permission to execute, notify or trade.
"""
from __future__ import annotations

from datetime import datetime

from quant_backtest.quant_sector.upstream_inbox import (
    QuantUpstreamReport,
    UpstreamInbox,
    UpstreamReceipt,
)
from quant_backtest.watch_dispatch.signal_pool import (
    STATUS_OPEN,
    SignalPoolEntry,
)


class OfflineUpstreamPipelineError(ValueError):
    """A synthetic signal failed the offline intake preconditions."""


def accept_pool_entry(
    entry: SignalPoolEntry,
    *,
    report_id: str,
    receiver_agent_id: str,
    inbox: UpstreamInbox,
    now: datetime,
) -> UpstreamReceipt:
    """Submit one synthetic OPEN entry to a caller-owned, in-memory inbox.

    A fresh QuantUpstreamReport binds the entry's event/evidence and producer
    identity to the independently supplied report ID and receiver. No pool
    transition, external acceptance or runtime delivery is attempted. Inbox
    errors (including future reports, conflicts and revocations) propagate.
    """
    if type(entry) is not SignalPoolEntry:
        raise OfflineUpstreamPipelineError("SIGNAL_ENTRY_INVALID")
    if type(inbox) is not UpstreamInbox:
        raise OfflineUpstreamPipelineError("INBOX_INVALID")
    # Reconstruct the immutable DTO to reject malformed, object-level forged
    # instances before the caller-owned inbox is allowed to mutate its ledger.
    try:
        checked = SignalPoolEntry(
            signal_id=entry.signal_id,
            producer_agent_id=entry.producer_agent_id,
            report_kind=entry.report_kind,
            event_id=entry.event_id,
            evidence_hash=entry.evidence_hash,
            evidence_version=entry.evidence_version,
            created_at=entry.created_at,
            co_signed=entry.co_signed,
            status=entry.status,
            epoch=entry.epoch,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise OfflineUpstreamPipelineError("SIGNAL_ENTRY_INVALID") from exc
    if checked.status != STATUS_OPEN or checked.epoch != 0:
        raise OfflineUpstreamPipelineError("SIGNAL_NOT_OPEN")

    # The report constructor and inbox enforce labels, distinct sender and
    # receiver identities, strict UTC and created_at <= now respectively.
    report = QuantUpstreamReport(
        report_id=report_id,
        report_kind=checked.report_kind,
        event_id=checked.event_id,
        evidence_hash=checked.evidence_hash,
        evidence_version=checked.evidence_version,
        sender_agent_id=checked.producer_agent_id,
        receiver_agent_id=receiver_agent_id,
        created_at=checked.created_at,
        non_executable=True,
    )
    return inbox.accept(report, now=now)
