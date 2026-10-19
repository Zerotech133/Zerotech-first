"""G4 read-only projection tests. No writes, no scripts, no connections."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from quant_backtest.watch_dispatch import (
    AuthorizationScope, HumanAuthorization, PermissionGate, ResearchReview,
    RiskDecision, RouteOutbox, ShadowDecision, ShadowLedger, ShadowReview,
    freeze_offline,
)
from quant_backtest.watch_dispatch.projections import (
    CONNECTION_STATUS, ProjectionError, evidence_projection, gate_projection,
    outbox_projection, render_readonly_html, shadow_projection,
)

T = datetime(2026, 10, 19, 2, 0, tzinfo=timezone.utc)
H = "a" * 64


def sample(**changes):
    fields = dict(schema_version=1, event_id="ev1", evidence_version=1,
                  previous_version_hash=None, pool="B", tenant_or_owner_id="owner1",
                  market="SH", code="600000", source_membership_id="member1",
                  source_revision="rev1", rule_id="r1", rule_version=1,
                  comparator="GE", threshold="1.00", unit="ratio", window="5m",
                  observed_value="1.10", baseline_value="1.00", raw_ref="raw:1",
                  raw_digest=H, source_id="src1", calendar_id="cal1",
                  source_as_of=T, visible_at=T, detected_at=T, freeze_at=T,
                  valid_until=T + timedelta(minutes=30), facts=("observed",),
                  hypotheses=("candidate",), supporting_refs=("raw:1",),
                  contradicting_refs=(), confirmation_condition="confirmed later",
                  invalidation_condition="source revoked")
    fields.update(changes)
    return freeze_offline(**fields)


def test_evidence_projection_reflects_store_state():
    envelope = sample()
    from quant_backtest.watch_dispatch import EvidenceStore
    store = EvidenceStore()
    summary = evidence_projection(envelope, store)
    assert summary["stored"] is False and summary["is_latest"] is False
    store.freeze(envelope)
    summary = evidence_projection(envelope, store)
    assert summary["stored"] is True and summary["is_latest"] is True
    assert summary["production_permitted"] is False
    assert summary["connection"] == CONNECTION_STATUS == "NOT_CONNECTED"


def test_outbox_projection_read_only_and_unplanned_rows():
    envelope = sample()
    outbox = RouteOutbox()
    routes = (("FEISHU_INFO", "desk", "u1"),
              ("RESEARCH_PENDING", "desk", "a1"),
              ("EXCHANGE_OBSERVE", "desk", "desk1"))
    rows = outbox_projection(envelope, outbox, routes)
    assert all(row["status"] == "UNPLANNED" for row in rows)
    assert all(row["executable"] is False for row in rows)
    outbox.plan(envelope, route="EXCHANGE_OBSERVE", target_type="desk",
                target_id="desk1", policy_version="p1", control_epoch=0,
                intent_id="i-1", created_at=T, valid_until=T + timedelta(minutes=10))
    rows = outbox_projection(envelope, outbox, routes)
    by_route = {row["route"]: row for row in rows}
    assert by_route["EXCHANGE_OBSERVE"]["status"] == "READY_OFFLINE"
    assert by_route["FEISHU_INFO"]["status"] == "UNPLANNED"
    assert outbox.get_attempts(envelope, route="EXCHANGE_OBSERVE",
                               target_type="desk", target_id="desk1") == ()


def test_shadow_projection_time_split_and_never_executable():
    envelope = sample()
    ledger = ShadowLedger()
    ledger.append_decision(envelope, ShadowDecision(
        shadow_id="sh-1", event_id=envelope.event_id,
        evidence_version=envelope.evidence_version,
        envelope_hash=envelope.envelope_hash, decision_at=T,
        hypothesis="breakout", planned_action="observe",
        invalidation_condition="close below", visible_evidence_refs=("raw:1",)))
    ledger.append_review(envelope, ShadowReview(
        review_id="sr-1", shadow_id="sh-1", reviewed_at=T + timedelta(minutes=10),
        posterior_refs=("post:1",), hindsight_note="held"))
    early = shadow_projection(envelope, ledger, T)
    assert len(early) == 1 and early[0]["visible_review_ids"] == ()
    later = shadow_projection(envelope, ledger, T + timedelta(minutes=11))
    assert later[0]["visible_review_ids"] == ("sr-1",)
    assert later[0]["non_executable"] is True
    assert later[0]["trading_permitted"] is False
    with pytest.raises(ProjectionError, match="AS_OF_INVALID"):
        shadow_projection(envelope, ledger, T.replace(tzinfo=None))


def test_gate_projection_is_constants_only():
    envelope = sample()
    summary = gate_projection(envelope, PermissionGate())
    assert summary["dispatch_permitted"] is False
    assert summary["trading_permitted"] is False
    assert summary["production_permitted"] is False
    assert summary["connection"] == "NOT_CONNECTED"


def test_render_html_static_escaped_and_script_free():
    envelope = sample()
    outbox = RouteOutbox()
    routes = (("FEISHU_INFO", "desk", "u1"),)
    rows = outbox_projection(envelope, outbox, routes)
    hostile = dict(rows[0], target_id="u<script>alert(1)</script>")
    shadow_rows = shadow_projection(envelope, ShadowLedger(), T)
    page = render_readonly_html(
        title="盯盘分发 <只读>",
        evidence=evidence_projection(envelope,
                                     __import__("quant_backtest.watch_dispatch",
                                                fromlist=["EvidenceStore"]).EvidenceStore()),
        outbox_rows=(hostile,), shadow_rows=shadow_rows,
        gate=gate_projection(envelope, PermissionGate()))
    assert "<script" not in page.lower()
    assert CONNECTION_STATUS in page
    assert "盯盘分发 &lt;只读&gt;" in page
    assert "u&lt;script&gt;" in page
    assert "UNPLANNED" in page
