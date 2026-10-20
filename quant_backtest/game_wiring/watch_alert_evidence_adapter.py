"""Synthetic WATCH1-to-G0 evidence bridge; offline memory only, never authority.

WATCH1 validates a self-reported claim. Neither its source_hash nor its membership
or alert identifiers attest original bytes, membership, or production provenance.
The caller must explicitly supply a *synthetic* fixture for the fields WATCH1
cannot provide. This module does not read a source, issue a receipt, route an
intent, notify a recipient, or authorize a trade.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from quant_backtest.watch_alert_contract import validate_alert
from quant_backtest.watch_dispatch.contracts import EvidenceEnvelope, freeze_offline
from quant_backtest.watch_dispatch.evidence_store import EvidenceStore


@dataclass(frozen=True)
class SyntheticEvidenceFixture:
    """Explicit, untrusted supplementary values for a synthetic offline test.

    raw_digest must be supplied independently; WATCH1 source_hash is only a
    canonical JSON hash of the self-reported snapshot, NOT an original-byte
    digest. source_membership_id is a correlation label, not a membership proof.
    No field here upgrades SOURCE_UNVERIFIED to a trusted qualification.
    """

    synthetic_only: bool
    tenant_or_owner_id: str
    source_membership_id: str
    source_revision: str
    rule_version: int
    unit: str
    window: str
    baseline_value: str
    raw_ref: str
    raw_digest: str
    source_id: str
    calendar_id: str
    detected_at: datetime
    freeze_at: datetime
    valid_until: datetime
    confirmation_condition: str
    invalidation_condition: str


def freeze_synthetic_watch_alert(
    payload: Mapping[str, Any],
    *,
    evaluation_as_of: datetime,
    fixture: SyntheticEvidenceFixture,
    store: EvidenceStore,
) -> EvidenceEnvelope:
    """Validate one shadow alert, then freeze a SOURCE_UNVERIFIED v1 observation.

    All checks precede the sole store mutation. Rejections leave this in-memory
    store without a consumable record for the rejected envelope. A repeated
    identical call is idempotent; conflicting version-one contents fail closed.
    There is deliberately no production, delivery, or trading path.
    """
    if type(fixture) is not SyntheticEvidenceFixture or fixture.synthetic_only is not True:
        raise ValueError("SYNTHETIC_FIXTURE_REQUIRED")
    if type(store) is not EvidenceStore:
        raise ValueError("MEMORY_STORE_REQUIRED")
    if not isinstance(payload, Mapping):
        raise ValueError("WATCH_ALERT_NOT_TRIGGERED:SCHEMA_FIELDS")
    result = validate_alert(payload, evaluation_as_of=evaluation_as_of)
    if result.status != "TRIGGERED" or result.code != "OK" or result.event is None:
        raise ValueError("WATCH_ALERT_NOT_TRIGGERED:" + result.code)
    event = result.event
    if event["run_mode"] != "SHADOW" or any(
        event[name]["status"] != "NOT_ATTEMPTED"
        for name in ("notification", "sim_handoff")
    ):
        raise ValueError("SHADOW_NO_SIDE_EFFECT_REQUIRED")
    if event["evidence_assessment"] != "SUPPORTED" or event["contradicting_refs"]:
        raise ValueError("CONTESTED_EVIDENCE_FORBIDDEN")
    if fixture.source_membership_id != event["membership_event"]["event_id"]:
        raise ValueError("MEMBERSHIP_CORRELATION_MISMATCH")
    if event["pool"] == "B" and fixture.tenant_or_owner_id != event["owner"]:
        raise ValueError("OWNER_CORRELATION_MISMATCH")
    if fixture.raw_digest == event["source_hash"] or fixture.raw_ref == event["source_ref"]:
        raise ValueError("SELF_REPORTED_SOURCE_IS_NOT_RAW_PROOF")

    comparator = {"GT": "GT", "GTE": "GE", "LT": "LT", "LTE": "LE",
                  "EQ": "EQ", "NE": "NE"}[event["comparator"]]
    source_as_of = datetime.fromisoformat(event["quote_as_of"].replace("Z", "+00:00"))
    visible_at = datetime.fromisoformat(event["visible_at"].replace("Z", "+00:00"))
    envelope = freeze_offline(
        schema_version=1,
        event_id=event["alert_id"],
        evidence_version=1,
        previous_version_hash=None,
        pool=event["pool"],
        tenant_or_owner_id=fixture.tenant_or_owner_id,
        market=event["market"],
        code=event["code"],
        source_membership_id=fixture.source_membership_id,
        source_revision=fixture.source_revision,
        rule_id=event["rule"],
        rule_version=fixture.rule_version,
        comparator=comparator,
        threshold=event["threshold"],
        unit=fixture.unit,
        window=fixture.window,
        observed_value=event["observed"],
        baseline_value=fixture.baseline_value,
        raw_ref=fixture.raw_ref,
        raw_digest=fixture.raw_digest,
        source_id=fixture.source_id,
        calendar_id=fixture.calendar_id,
        source_as_of=source_as_of,
        visible_at=visible_at,
        detected_at=fixture.detected_at,
        freeze_at=fixture.freeze_at,
        valid_until=fixture.valid_until,
        facts=tuple(event["facts"]),
        hypotheses=tuple(event["hypotheses"]),
        supporting_refs=tuple(event["supporting_refs"]),
        contradicting_refs=tuple(event["contradicting_refs"]),
        confirmation_condition=fixture.confirmation_condition,
        invalidation_condition=fixture.invalidation_condition,
    )
    envelope.validate(now=evaluation_as_of)
    return store.freeze(envelope, now=evaluation_as_of)
