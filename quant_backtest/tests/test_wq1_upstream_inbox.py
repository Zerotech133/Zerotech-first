"""W-Q1 upstream inbox tests: idempotent ACK, fail-closed acceptance.

Offline only; no network, no persistence, no execution semantics.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from quant_backtest.quant_sector.upstream_inbox import (
    QuantUpstreamReport,
    UpstreamAck,
    UpstreamInbox,
    UpstreamInboxError,
    UpstreamReceipt,
)

T = datetime(2026, 10, 19, 2, 0, tzinfo=timezone.utc)
H = "a" * 64
SOURCE = "upstream-inbox-v1"


def make_report(**changes) -> QuantUpstreamReport:
    fields = dict(report_id="rep1", report_kind="ANOMALY", event_id="ev1",
                  evidence_hash=H, evidence_version=1,
                  sender_agent_id="watch-agent-1",
                  receiver_agent_id="quant-sector-agent",
                  created_at=T)
    fields.update(changes)
    return QuantUpstreamReport(**fields)


def test_accept_issues_receipt_and_ack():
    inbox = UpstreamInbox()
    report = make_report()
    receipt = inbox.accept(report, now=T)
    assert type(receipt) is UpstreamReceipt
    assert receipt.receipt_id == inbox.get(receipt.receipt_id).receipt_id
    assert receipt.report_id == "rep1"
    assert receipt.envelope_hash == report.envelope_hash
    assert receipt.sender_agent_id == "watch-agent-1"
    assert receipt.ledger_epoch == 0
    ack = receipt.ack
    assert type(ack) is UpstreamAck
    assert ack.report_id == report.report_id
    assert ack.receiver_agent_id == report.receiver_agent_id
    assert ack.verification_source == SOURCE
    assert ack.acked_at >= receipt.accepted_at
    # No object carries any trading permission concept.
    for obj in (report, receipt, ack, inbox):
        assert not hasattr(obj, "trading_permitted")


def test_accept_idempotent_same_receipt_and_ack():
    inbox = UpstreamInbox()
    report = make_report()
    first = inbox.accept(report, now=T)
    second = inbox.accept(report, now=T + timedelta(minutes=5))
    assert first is second
    assert first.ack is second.ack
    assert first.accepted_at == second.accepted_at
    assert len(inbox.list_accepted()) == 1


def test_same_report_id_different_content_conflicts():
    inbox = UpstreamInbox()
    inbox.accept(make_report(), now=T)
    twin = make_report(evidence_hash="b" * 64)
    with pytest.raises(UpstreamInboxError, match="RECEIPT_CONFLICT"):
        inbox.accept(twin, now=T + timedelta(seconds=1))
    assert len(inbox.list_accepted()) == 1


def test_invalid_report_kind_rejected():
    inbox = UpstreamInbox()
    with pytest.raises(UpstreamInboxError, match="REPORT_KIND_INVALID"):
        make_report(report_kind="TRADE_SIGNAL")
    with pytest.raises(UpstreamInboxError, match="REPORT_KIND_INVALID"):
        make_report(report_kind="QUANT_UPSTREAM_REPORT")


def test_naive_times_rejected():
    naive = datetime(2026, 10, 19, 2, 0)
    inbox = UpstreamInbox()
    with pytest.raises(UpstreamInboxError, match="ACCEPTED_AT_INVALID"):
        inbox.accept(make_report(), now=naive)
    with pytest.raises(UpstreamInboxError, match="REPORT_CREATED_AT_INVALID"):
        make_report(created_at=naive)
    with pytest.raises(UpstreamInboxError, match="REPORT_CREATED_AT_INVALID"):
        # Non-zero offset is not canonical UTC either.
        make_report(created_at=datetime(
            2026, 10, 19, 10, 0, tzinfo=timezone(timedelta(hours=8))))


def test_revoke_epoch_only_moves_forward():
    inbox = UpstreamInbox()
    receipt = inbox.accept(make_report(), now=T)
    assert inbox.ledger_epoch == 0
    epoch = inbox.revoke(receipt.receipt_id, now=T + timedelta(minutes=1))
    assert epoch == 1 and inbox.ledger_epoch == 1
    with pytest.raises(UpstreamInboxError, match="RECEIPT_ALREADY_REVOKED"):
        inbox.revoke(receipt.receipt_id, now=T + timedelta(minutes=2))
    assert inbox.ledger_epoch == 1
    with pytest.raises(UpstreamInboxError, match="RECEIPT_UNKNOWN"):
        inbox.revoke("missing-receipt", now=T + timedelta(minutes=2))
    assert inbox.ledger_epoch == 1


def test_revoke_before_ack_rejected_without_changing_active_receipt():
    inbox = UpstreamInbox()
    accepted_at = T + timedelta(minutes=5)
    receipt = inbox.accept(make_report(), now=accepted_at)
    ack = receipt.ack

    with pytest.raises(UpstreamInboxError, match="^REVOKE_TIME_INVALID$"):
        inbox.revoke(receipt.receipt_id, now=T)

    assert inbox.ledger_epoch == 0
    assert inbox.get(receipt.receipt_id) is receipt
    assert inbox.receipt_for_report(receipt.report_id) is receipt
    assert receipt.ack is ack
    assert inbox.is_active(receipt.receipt_id) is True
    assert inbox.is_revoked(receipt.report_id) is False
    assert inbox.accept(make_report(), now=accepted_at) is receipt

    # Equal UTC instants are valid; failed attempts have not consumed an epoch.
    assert inbox.revoke(receipt.receipt_id, now=ack.acked_at) == 1
    assert inbox.ledger_epoch == 1
    assert inbox.is_active(receipt.receipt_id) is False
    assert inbox.is_revoked(receipt.report_id) is True


def test_accept_after_revoke_fails_closed():
    inbox = UpstreamInbox()
    receipt = inbox.accept(make_report(), now=T)
    inbox.revoke(receipt.receipt_id, now=T + timedelta(minutes=1))
    with pytest.raises(UpstreamInboxError, match="RECEIPT_REVOKED"):
        inbox.accept(make_report(), now=T + timedelta(minutes=2))
    # Even the byte-identical report cannot be re-accepted after revoke.
    with pytest.raises(UpstreamInboxError, match="RECEIPT_REVOKED"):
        inbox.accept(make_report(), now=T + timedelta(minutes=2))
    assert inbox.ledger_epoch == 1


def test_read_accessors_are_snapshot_only():
    inbox = UpstreamInbox()
    first = inbox.accept(make_report(), now=T)
    second = inbox.accept(
        make_report(report_id="rep2", report_kind="HEALTH",
                    event_id="ev2"), now=T + timedelta(seconds=1))
    listed = inbox.list_accepted()
    assert listed == (first, second)
    assert inbox.receipt_for_report("rep2") is second
    assert inbox.receipt_for_report("missing") is None
    assert inbox.get("missing") is None
    assert inbox.is_active(first.receipt_id) is True
    assert inbox.is_active(second.receipt_id) is True
    inbox.revoke(first.receipt_id, now=T + timedelta(minutes=1))
    assert inbox.is_active(first.receipt_id) is False
    assert inbox.is_revoked("rep1") is True
    assert inbox.is_revoked("rep2") is False
