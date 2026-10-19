"""Read-only projections over offline watch_dispatch state. Never a writer.

Consumes only the public read accessors of the frozen G0-G2 modules. Every
projection carries NOT_CONNECTED until real adapters are separately approved.
HTML rendering is static, escaped and script-free.
"""
from __future__ import annotations

import html
from datetime import datetime
from typing import Dict, Iterable, Tuple

from quant_backtest.watch_dispatch.canonical import utc_text
from quant_backtest.watch_dispatch.contracts import EvidenceEnvelope
from quant_backtest.watch_dispatch.evidence_store import EvidenceStore
from quant_backtest.watch_dispatch.permission_gate import PermissionGate
from quant_backtest.watch_dispatch.route_outbox import RouteOutbox
from quant_backtest.watch_dispatch.shadow_ledger import ShadowLedger

CONNECTION_STATUS = "NOT_CONNECTED"
_UNPLANNED = "UNPLANNED"


class ProjectionError(ValueError):
    """Fail-closed projection input violation."""


def _utc(value: object, field: str) -> None:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ProjectionError(field + "_INVALID")
    utc_text(value)  # type: ignore[arg-type]


def evidence_projection(envelope: EvidenceEnvelope,
                        store: EvidenceStore) -> Dict[str, object]:
    """Identity and availability summary; no content writes are possible."""
    envelope.validate()
    stored = store.get_exact(envelope)
    latest = store.latest(envelope)
    return {
        "event_id": envelope.event_id,
        "evidence_version": envelope.evidence_version,
        "envelope_hash": envelope.envelope_hash,
        "pool": envelope.pool,
        "market": envelope.market,
        "code": envelope.code,
        "source_qualification": envelope.source_qualification,
        "stored": stored is not None,
        "is_latest": latest is not None and latest.envelope_hash == envelope.envelope_hash,
        "production_permitted": envelope.production_permitted,
        "connection": CONNECTION_STATUS,
    }


def outbox_projection(envelope: EvidenceEnvelope, outbox: RouteOutbox,
                      routes: Iterable[Tuple[str, str, str]]) -> Tuple[Dict[str, object], ...]:
    """Per-target status rows; identical input yields identical output."""
    envelope.validate()
    rows: list[Dict[str, object]] = []
    for route, target_type, target_id in routes:
        intent = outbox.get_intent(envelope, route=route, target_type=target_type,
                                   target_id=target_id)
        attempts = outbox.get_attempts(envelope, route=route,
                                       target_type=target_type, target_id=target_id)
        last = attempts[-1] if attempts else None
        rows.append({
            "route": route,
            "target_type": target_type,
            "target_id": target_id,
            "status": intent.status if intent is not None else _UNPLANNED,
            "attempt_count": len(attempts),
            "last_outcome": last.outcome if last is not None else None,
            "last_receipt_at": (last.receipt.observed_at if last is not None
                                and last.receipt is not None else None),
            "executable": False,
            "connection": CONNECTION_STATUS,
        })
    return tuple(rows)


def shadow_projection(envelope: EvidenceEnvelope, ledger: ShadowLedger,
                      as_of: datetime) -> Tuple[Dict[str, object], ...]:
    """Time-split training views; posterior items after as_of stay invisible."""
    _utc(as_of, "AS_OF")
    rows: list[Dict[str, object]] = []
    for view in ledger.read_asof(envelope, as_of):
        rows.append({
            "shadow_id": view.decision.shadow_id,
            "decision_at": view.decision.decision_at,
            "hypothesis": view.decision.hypothesis,
            "planned_action": view.decision.planned_action,
            "invalidation_condition": view.decision.invalidation_condition,
            "non_executable": view.decision.non_executable,
            "visible_review_ids": tuple(r.review_id for r in view.reviews_up_to),
            "hidden_future_reviews": None,
            "trading_permitted": view.decision.trading_permitted,
            "connection": CONNECTION_STATUS,
        })
    return tuple(rows)


def gate_projection(envelope: EvidenceEnvelope, gate: PermissionGate) -> Dict[str, object]:
    """Constants-only summary; live dual-gate checks go through gate.consume()."""
    envelope.validate()
    return {
        "dispatch_permitted": gate.dispatch_permitted,
        "trading_permitted": gate.trading_permitted,
        "notification_permitted": False,
        "production_permitted": False,
        "live_reverification": "gate.consume",
        "connection": CONNECTION_STATUS,
    }


def render_readonly_html(*, title: str, evidence: Dict[str, object],
                         outbox_rows: Tuple[Dict[str, object], ...],
                         shadow_rows: Tuple[Dict[str, object], ...],
                         gate: Dict[str, object]) -> str:
    """Static escaped HTML fragment; no script tags, no external calls."""
    if not title or not all(type(t) is str for t in (title,)):
        raise ProjectionError("TITLE_INVALID")
    def esc(value: object) -> str:
        return html.escape(str(value))
    def table(headers: tuple[str, ...], rows: Iterable[Dict[str, object]],
              keys: tuple[str, ...]) -> str:
        body = "".join(
            "<tr>" + "".join(f"<td>{esc(row.get(key))}</td>" for key in keys) + "</tr>"
            for row in rows)
        head = "".join(f"<th>{esc(header)}</th>" for header in headers)
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    evidence_items = "".join(
        f"<li><b>{esc(key)}</b>: {esc(value)}</li>" for key, value in evidence.items())
    gate_items = "".join(
        f"<li><b>{esc(key)}</b>: {esc(value)}</li>" for key, value in gate.items())
    return (
        f'<section data-connection="{CONNECTION_STATUS}">'
        f"<h2>{esc(title)}</h2>"
        f'<p class="status">Connection: {CONNECTION_STATUS} (offline projection; '
        "no delivery, no dispatch)</p>"
        f"<h3>Evidence</h3><ul>{evidence_items}</ul>"
        "<h3>Routes</h3>" + table(
            ("route", "target", "status", "attempts", "last_outcome"), outbox_rows,
            ("route", "target_id", "status", "attempt_count", "last_outcome")) +
        "<h3>Shadow decisions (visible at as-of)</h3>" + table(
            ("shadow_id", "decision_at", "hypothesis", "reviews"), shadow_rows,
            ("shadow_id", "decision_at", "hypothesis", "visible_review_ids")) +
        f"<h3>Gates</h3><ul>{gate_items}</ul></section>")
