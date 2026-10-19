"""W-Q1 cross-module adversarial tests: route enums are mutually exclusive.

The watch side (watch_dispatch) only ever accepts QUANT_UPSTREAM_REPORT;
the quant sector side (quant_sector) only ever accepts the three
distribution routes. Each side rejects the other's routes fail-closed.
These are the ONLY files allowed to import both packages together.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from quant_backtest.quant_sector.dispatch_ledger import (
    DispatchIntent,
    DispatchLedger,
    DispatchLedgerError,
)
from quant_backtest.quant_sector.upstream_inbox import (
    QuantUpstreamReport,
    UpstreamInbox,
    UpstreamInboxError,
)
from quant_backtest.watch_dispatch import (
    EvidenceEnvelope,
    RouteIntent,
    freeze_offline,
)
from quant_backtest.watch_dispatch.route_outbox import OutboxError, RouteOutbox

T = datetime(2026, 10, 19, 2, 0, tzinfo=timezone.utc)
H = "a" * 64
QUANT_ROUTES = ("FEISHU_INFO", "RESEARCH_REVIEW", "EXCHANGE_OBSERVATION")


def sample_envelope() -> EvidenceEnvelope:
    fields = dict(schema_version=1, event_id="ev1", evidence_version=1,
                  previous_version_hash=None, pool="B",
                  tenant_or_owner_id="owner1", market="SH", code="600000",
                  source_membership_id="member1", source_revision="rev1",
                  rule_id="r1", rule_version=1, comparator="GE",
                  threshold="1.00", unit="ratio", window="5m",
                  observed_value="1.10", baseline_value="1.00",
                  raw_ref="raw:1", raw_digest=H, source_id="src1",
                  calendar_id="cal1", source_as_of=T, visible_at=T,
                  detected_at=T, freeze_at=T,
                  valid_until=T + timedelta(minutes=30),
                  facts=("observed",), hypotheses=("candidate",),
                  supporting_refs=("raw:1",), contradicting_refs=(),
                  confirmation_condition="confirmed later",
                  invalidation_condition="source revoked")
    return freeze_offline(**fields)


def test_watch_side_rejects_all_three_quant_routes():
    envelope = sample_envelope()
    outbox = RouteOutbox()
    for route in QUANT_ROUTES:
        with pytest.raises(OutboxError, match="ROUTE_INVALID"):
            outbox.plan(envelope, route=route, target_type="desk",
                        target_id="t1", policy_version="p1", control_epoch=0,
                        intent_id=f"intent-{route}",
                        created_at=envelope.freeze_at,
                        valid_until=envelope.valid_until - timedelta(minutes=1))
        intent = RouteIntent(
            intent_id=f"intent-{route}", event_id=envelope.event_id,
            evidence_version=envelope.evidence_version,
            envelope_hash=envelope.envelope_hash, route=route,
            target_type="desk", target_id="t1",
            tenant_scope=envelope.tenant_or_owner_id,
            payload_digest=envelope.envelope_hash, policy_version="p1",
            control_epoch=0, created_at=envelope.freeze_at,
            valid_until=envelope.valid_until - timedelta(minutes=1))
        with pytest.raises(ValueError, match="ROUTE_INVALID"):
            intent.validate(envelope)


def test_quant_sector_rejects_watch_upstream_route():
    inbox = UpstreamInbox()
    for route in ("QUANT_UPSTREAM_REPORT",):
        with pytest.raises(DispatchLedgerError, match="ROUTE_INVALID"):
            DispatchLedger(route, inbox_lookup=inbox.is_active)
        with pytest.raises(DispatchLedgerError, match="ROUTE_INVALID"):
            DispatchIntent(intent_id="int-w", receipt_id="rcp-w",
                           route=route, target_ref="agent:quant-sector",
                           created_at=T)
    # The inbox likewise rejects the watch-side report kind vocabulary.
    with pytest.raises(UpstreamInboxError, match="REPORT_KIND_INVALID"):
        QuantUpstreamReport(
            report_id="rep-w", report_kind="QUANT_UPSTREAM_REPORT",
            event_id="ev1", evidence_hash=H, evidence_version=1,
            sender_agent_id="watch-agent-1",
            receiver_agent_id="quant-sector-agent", created_at=T)


def test_route_matrix_is_strictly_partitioned():
    """Each route is valid on exactly one side and invalid on the other."""
    envelope = sample_envelope()
    outbox = RouteOutbox()
    inbox = UpstreamInbox()
    # Watch accepts only QUANT_UPSTREAM_REPORT.
    planned = outbox.plan(envelope, route="QUANT_UPSTREAM_REPORT",
                          target_type="AGENT",
                          target_id="quant-sector-agent",
                          policy_version="agent-report-v1", control_epoch=0,
                          intent_id="intent-up",
                          created_at=envelope.freeze_at,
                          valid_until=envelope.valid_until
                          - timedelta(minutes=1))
    assert planned.route == "QUANT_UPSTREAM_REPORT"
    # The quant sector rejects that same route.
    with pytest.raises(DispatchLedgerError, match="ROUTE_INVALID"):
        DispatchLedger("QUANT_UPSTREAM_REPORT", inbox_lookup=inbox.is_active)
    # The three sector routes are valid sector-side (constructible) and
    # invalid watch-side.
    inbox.accept(
        QuantUpstreamReport(
            report_id="rep-m", report_kind="ANOMALY", event_id="ev-m",
            evidence_hash=H, evidence_version=1,
            sender_agent_id="watch-agent-1",
            receiver_agent_id="quant-sector-agent", created_at=T),
        now=T)
    receipt = inbox.receipt_for_report("rep-m")
    for route in QUANT_ROUTES:
        ledger = DispatchLedger(route, inbox_lookup=inbox.is_active)
        intent = DispatchIntent(intent_id=f"int-m-{route}",
                                receipt_id=receipt.receipt_id, route=route,
                                target_ref="probe", created_at=T)
        assert ledger.plan(intent, receipt_view=receipt, now=T) is intent
        with pytest.raises(OutboxError, match="ROUTE_INVALID"):
            outbox.plan(envelope, route=route, target_type="desk",
                        target_id="t1", policy_version="p1", control_epoch=0,
                        intent_id=f"intent-w-{route}",
                        created_at=envelope.freeze_at,
                        valid_until=envelope.valid_until
                        - timedelta(minutes=1))
