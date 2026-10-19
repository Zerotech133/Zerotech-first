"""Read-only projections over quant_sector state. Never a writer.

W-Q1 approved scope. Consumes only the public read accessors of the
upstream inbox and the three dispatch ledgers. Every projection carries
NOT_CONNECTED until real delivery adapters are separately approved. HTML
rendering is static, fully escaped and script-free.
"""
from __future__ import annotations

import html
from typing import Dict, Iterable, Tuple

from quant_backtest.quant_sector.dispatch_ledger import DispatchLedger
from quant_backtest.quant_sector.upstream_inbox import (
    UpstreamInbox,
    utc_text,
)

CONNECTION_STATUS = "NOT_CONNECTED"


class ProjectionError(ValueError):
    """Fail-closed projection input violation."""


def inbox_snapshot(inbox: UpstreamInbox) -> Tuple[Dict[str, object], ...]:
    """Read-only acceptance-ledger snapshot; identical input, identical output."""
    if type(inbox) is not UpstreamInbox:
        raise ProjectionError("INBOX_INVALID")
    rows: list[Dict[str, object]] = []
    for receipt in inbox.list_accepted():
        rows.append({
            "receipt_id": receipt.receipt_id,
            "report_id": receipt.report_id,
            "envelope_hash": receipt.envelope_hash,
            "sender_agent_id": receipt.sender_agent_id,
            "receiver_agent_id": receipt.ack.receiver_agent_id,
            "accepted_at": utc_text(receipt.accepted_at),
            "ack_id": receipt.ack.ack_id,
            "acked_at": utc_text(receipt.ack.acked_at),
            "verification_source": receipt.ack.verification_source,
            "ledger_epoch": receipt.ledger_epoch,
            "revoked": inbox.is_revoked(receipt.report_id),
            "connection": CONNECTION_STATUS,
        })
    return tuple(rows)


def dispatch_snapshot(ledger: DispatchLedger) -> Tuple[Dict[str, object], ...]:
    """Read-only per-route dispatch snapshot; identical input, identical output."""
    if type(ledger) is not DispatchLedger:
        raise ProjectionError("LEDGER_INVALID")
    rows: list[Dict[str, object]] = []
    for intent in ledger.list_intents():
        attempts = ledger.get_attempts(intent.intent_id)
        last = attempts[-1] if attempts else None
        rows.append({
            "intent_id": intent.intent_id,
            "route": intent.route,
            "receipt_id": intent.receipt_id,
            "target_ref": intent.target_ref,
            "created_at": utc_text(intent.created_at),
            "status": ledger.status(intent.intent_id),
            "attempt_count": len(attempts),
            "last_attempt_no": last.attempt_no if last is not None else None,
            "last_outcome": last.outcome if last is not None else None,
            "external_message_id": (
                last.external_message_id if last is not None else None),
            "observed_at": (
                utc_text(last.observed_at)
                if last is not None and last.observed_at is not None else None),
            "connection": CONNECTION_STATUS,
        })
    return tuple(rows)


def render_readonly_html(*, title: str,
                         inbox_rows: Iterable[Dict[str, object]],
                         dispatch_rows: Iterable[Dict[str, object]]) -> str:
    """Static escaped HTML fragment; no script tags, no forms, no delivery."""
    if type(title) is not str or not title:
        raise ProjectionError("TITLE_INVALID")

    def esc(value: object) -> str:
        return html.escape(str(value))

    def table(headers: Tuple[str, ...], rows: Iterable[Dict[str, object]],
              keys: Tuple[str, ...]) -> str:
        head = "".join(f"<th>{esc(header)}</th>" for header in headers)
        body = "".join(
            "<tr>" + "".join(f"<td>{esc(row.get(key))}</td>" for key in keys)
            + "</tr>"
            for row in rows)
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

    return (
        f'<section data-connection="{CONNECTION_STATUS}">'
        f"<h2>{esc(title)}</h2>"
        f'<p class="status">Connection: {CONNECTION_STATUS} '
        "(offline projection; no delivery, no dispatch, no execution)</p>"
        "<h3>Upstream inbox</h3>" + table(
            ("receipt_id", "report_id", "accepted_at", "ack_id",
             "epoch", "revoked"),
            inbox_rows,
            ("receipt_id", "report_id", "accepted_at", "ack_id",
             "ledger_epoch", "revoked")) +
        "<h3>Dispatch ledgers</h3>" + table(
            ("intent_id", "route", "status", "attempts", "last_outcome"),
            dispatch_rows,
            ("intent_id", "route", "status", "attempt_count",
             "last_outcome")) +
        "</section>")
