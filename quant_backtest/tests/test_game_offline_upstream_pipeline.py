"""Offline-only contract tests for synthetic pool-to-quant inbox adaptation."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from quant_backtest.game_wiring.offline_upstream_pipeline import (
    OfflineUpstreamPipelineError,
    accept_pool_entry,
)
from quant_backtest.quant_sector.upstream_inbox import (
    QuantUpstreamReport,
    UpstreamInbox,
    UpstreamInboxError,
)
from quant_backtest.watch_dispatch.signal_pool import SignalPoolEntry

NOW = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)


def _entry(**changes: object) -> SignalPoolEntry:
    """Build one synthetic, non-authoritative pool entry."""
    entry = SignalPoolEntry(
        signal_id="sig-053",
        producer_agent_id="watch-053",
        report_kind="ANOMALY",
        event_id="event-053",
        evidence_hash="a" * 64,
        evidence_version=1,
        created_at=NOW - timedelta(seconds=1),
    )
    return replace(entry, **changes)


def _accept(
    inbox: UpstreamInbox, entry: SignalPoolEntry | object | None = None,
    *, report_id: str = "report-053", receiver_agent_id: str = "quant-053",
    now: datetime = NOW,
):
    """Invoke the adapter while retaining explicit receiver and report ID."""
    return accept_pool_entry(
        _entry() if entry is None else entry,
        report_id=report_id,
        receiver_agent_id=receiver_agent_id,
        inbox=inbox,
        now=now,
    )


def test_synthetic_entry_maps_to_report_and_local_receipt() -> None:
    """Report envelope binds entry fields, not a caller's substitute hash."""
    inbox = UpstreamInbox()
    entry = _entry(report_kind="HEALTH", evidence_version=2)
    receipt = _accept(inbox, entry)
    report = QuantUpstreamReport(
        report_id="report-053",
        report_kind=entry.report_kind,
        event_id=entry.event_id,
        evidence_hash=entry.evidence_hash,
        evidence_version=entry.evidence_version,
        sender_agent_id=entry.producer_agent_id,
        receiver_agent_id="quant-053",
        created_at=entry.created_at,
    )
    assert receipt.envelope_hash == report.envelope_hash
    assert receipt.envelope_hash != entry.evidence_hash
    assert receipt.sender_agent_id == entry.producer_agent_id
    assert receipt.accepted_at == receipt.ack.acked_at == NOW
    assert receipt.ack.report_id == receipt.report_id == report.report_id
    assert receipt.ack.receiver_agent_id == "quant-053"
    assert receipt.ack.verification_source == "upstream-inbox-v1"
    assert receipt.ledger_epoch == inbox.ledger_epoch == 0
    assert inbox.get(receipt.receipt_id) is receipt
    assert entry.status == "OPEN"  # Local ACK cannot promote a pool entry.


@pytest.mark.parametrize("status", ["CLAIMED", "ACCEPTED", "REVOKED"])
def test_non_open_entries_fail_before_inbox_mutation(status: str) -> None:
    """Claims, accepted signals and revoked signals cannot be resubmitted."""
    inbox = UpstreamInbox()
    with pytest.raises(OfflineUpstreamPipelineError, match="SIGNAL_NOT_OPEN"):
        _accept(inbox, _entry(status=status))
    assert inbox.list_accepted() == ()


def test_nonzero_epoch_and_invalid_entry_type_fail_closed() -> None:
    """A recycled or forged entry is not a live OPEN version-zero signal."""
    inbox = UpstreamInbox()
    with pytest.raises(OfflineUpstreamPipelineError, match="SIGNAL_NOT_OPEN"):
        _accept(inbox, _entry(epoch=1))
    with pytest.raises(OfflineUpstreamPipelineError, match="SIGNAL_ENTRY_INVALID"):
        _accept(inbox, object())
    assert inbox.list_accepted() == ()


@pytest.mark.parametrize("receiver", ["watch-053", "", "bad receiver"])
def test_receiver_identity_is_explicit_and_validated(receiver: str) -> None:
    """Sender cannot be the receiver, and receiver labels cannot be invalid."""
    inbox = UpstreamInbox()
    with pytest.raises(UpstreamInboxError):
        _accept(inbox, receiver_agent_id=receiver)
    assert inbox.list_accepted() == ()


def test_time_gate_rejects_future_and_non_utc_before_mutation() -> None:
    """Future created_at and naive intake times cannot produce an ACK."""
    inbox = UpstreamInbox()
    with pytest.raises(UpstreamInboxError, match="REPORT_CREATED_AT_FUTURE"):
        _accept(inbox, _entry(created_at=NOW + timedelta(microseconds=1)))
    with pytest.raises(UpstreamInboxError, match="ACCEPTED_AT_INVALID"):
        _accept(inbox, now=NOW.replace(tzinfo=None))
    assert inbox.list_accepted() == ()


def test_exact_duplicate_is_idempotent_but_conflicting_report_id_fails() -> None:
    """A changed receiver or evidence under one report ID is a conflict."""
    inbox = UpstreamInbox()
    first = _accept(inbox)
    assert _accept(inbox) is first
    with pytest.raises(UpstreamInboxError, match="RECEIPT_CONFLICT"):
        _accept(inbox, receiver_agent_id="other-quant")
    with pytest.raises(UpstreamInboxError, match="RECEIPT_CONFLICT"):
        _accept(inbox, _entry(evidence_hash="b" * 64))
    assert inbox.list_accepted() == (first,)


def test_revoke_prevents_even_identical_reacceptance() -> None:
    """The inbox's append-only revocation is authoritative for its own ID."""
    inbox = UpstreamInbox()
    first = _accept(inbox)
    assert inbox.revoke(first.receipt_id, now=NOW) == 1
    with pytest.raises(UpstreamInboxError, match="RECEIPT_REVOKED"):
        _accept(inbox)
    assert not inbox.is_active(first.receipt_id)
    assert inbox.ledger_epoch == 1


def test_local_only_has_no_production_or_pool_side_effects() -> None:
    """Only the caller-supplied inbox changes; entry never changes state."""
    inbox = UpstreamInbox()
    entry = _entry()
    before = entry
    receipt = _accept(inbox, entry)
    assert entry is before and entry.status == "OPEN"
    assert not entry.production_permitted
    assert not entry.notification_permitted
    assert not entry.trading_permitted
    assert inbox.list_accepted() == (receipt,)
    # ACK is inbox-local: it cannot certify an independent external receipt.
    assert receipt.ack.verification_source == "upstream-inbox-v1"
