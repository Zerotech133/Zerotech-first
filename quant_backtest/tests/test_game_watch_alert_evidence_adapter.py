"""Synthetic-only WATCH1/G0 bridge regression; no I/O, source, or dispatch."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from quant_backtest.game_wiring.watch_alert_evidence_adapter import (
    SyntheticEvidenceFixture,
    freeze_synthetic_watch_alert,
)
from quant_backtest.watch_alert_contract import load_alert_json, make_alert_ids
from quant_backtest.watch_dispatch.evidence_store import EvidenceConflict, EvidenceStore


NOW = datetime(2026, 10, 19, 10, 5, tzinfo=timezone.utc)
RECEIPT = {"status": "NOT_ATTEMPTED", "receipt_id": None, "at": None}


def alert(*, pool: str = "A", comparator: str = "GTE", observed: str = "12") -> dict:
    """Build one self-consistent, explicitly synthetic WATCH1 claim."""
    event = {
        "schema_version": "WATCH1_V1", "alert_id": "", "trace_id": "", "dedupe_key": "",
        "pool": pool, "run_mode": "SHADOW", "market": "SSE", "code": "600519",
        "instrument_type": "STOCK", "strategy": "trend" if pool == "A" else None,
        "strategy_version": "1" if pool == "A" else None,
        "source_batch": "batch-1" if pool == "A" else None,
        "owner": "owner-1" if pool == "B" else None,
        "watch_rule": "price-break" if pool == "B" else None,
        "watch_rule_version": "1" if pool == "B" else None,
        "membership_event": {
            "event_id": "episode-1", "kind": "ENTER",
            "as_of": "2026-10-19T09:00:00Z", "market": "SSE", "code": "600519",
            "pool": pool,
            "qualification": ({"strategy": "trend", "strategy_version": "1",
                               "source_batch": "batch-1"} if pool == "A" else
                              {"owner": "owner-1", "watch_rule": "price-break",
                               "watch_rule_version": "1"}),
        },
        "rule": "above-10", "comparator": comparator, "threshold": "10",
        "observed": observed, "quote_as_of": "2026-10-19T10:00:00Z",
        "visible_at": "2026-10-19T10:01:00Z", "source_hash": "",
        "source_ref": "claim:row1", "source_snapshot": None,
        "source_kind": "DIRECT_QUOTE", "quality": "VERIFIED",
        "facts": ["quote:row1"], "hypotheses": ["possible breakout"],
        "supporting_refs": ["quote:row1"], "contradicting_refs": [],
        "evidence_assessment": "SUPPORTED", "notification": copy.deepcopy(RECEIPT),
        "sim_handoff": copy.deepcopy(RECEIPT),
    }
    source_fields = ("source_ref", "source_kind", "market", "code", "instrument_type",
                     "observed", "quote_as_of", "visible_at", "quality")
    event["source_snapshot"] = {key: event[key] for key in source_fields}
    event["source_hash"] = hashlib.sha256(json.dumps(
        event["source_snapshot"], ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    event.update(make_alert_ids(event))
    return event


def fixture(**changes: object) -> SyntheticEvidenceFixture:
    """Explicitly inject unverified, isolated test-only fields (not authority)."""
    values: dict = {
        "synthetic_only": True, "tenant_or_owner_id": "owner-1",
        "source_membership_id": "episode-1", "source_revision": "fixture-rev1",
        "rule_version": 1, "unit": "quote", "window": "instant",
        "baseline_value": "10", "raw_ref": "synthetic-fixture:bytes1",
        "raw_digest": "c" * 64, "source_id": "synthetic-fixture-source",
        "calendar_id": "synthetic-fixture-calendar",
        "detected_at": NOW - timedelta(minutes=3),
        "freeze_at": NOW - timedelta(minutes=2),
        "valid_until": NOW + timedelta(minutes=10),
        "confirmation_condition": "independently verify source and membership",
        "invalidation_condition": "unverified fixture expired or revoked",
    }
    values.update(changes)
    return SyntheticEvidenceFixture(**values)


@pytest.mark.parametrize("pool,comparator,expected", [
    ("A", "GTE", "GE"), ("B", "LTE", "LE"), ("A", "GT", "GT"),
])
def test_triggered_is_only_unverified_memory_observation(
    pool: str, comparator: str, expected: str,
) -> None:
    event = alert(pool=pool, comparator=comparator,
                  observed="8" if comparator == "LTE" else "12")
    store = EvidenceStore()
    frozen = freeze_synthetic_watch_alert(event, evaluation_as_of=NOW,
                                          fixture=fixture(), store=store)
    assert frozen.comparator == expected
    assert frozen.source_qualification == "SOURCE_UNVERIFIED"
    assert frozen.raw_digest != event["source_hash"]
    assert frozen.raw_ref != event["source_ref"]
    assert frozen.production_permitted is False
    assert frozen.notification_permitted is False
    assert frozen.trading_permitted is False
    assert store.get_exact(frozen) is frozen
    assert freeze_synthetic_watch_alert(event, evaluation_as_of=NOW,
                                        fixture=fixture(), store=store) is frozen


@pytest.mark.parametrize("change", [
    lambda e: e.update(observed="9"),
    lambda e: e.update(quality="STALE"),
    lambda e: e.update(evidence_assessment="CONTESTED",
                       facts=["quote:row1", "risk:row2"],
                       contradicting_refs=["risk:row2"]),
    lambda e: e.update(market="SZSE"),
])
def test_nontriggered_unjudgeable_contested_or_mismatch_cannot_freeze(change) -> None:
    event = alert()
    change(event)
    if event["observed"] == "9":
        event["source_snapshot"]["observed"] = "9"
        event["source_hash"] = hashlib.sha256(json.dumps(
            event["source_snapshot"], ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode("utf-8")).hexdigest()
    if event["evidence_assessment"] == "CONTESTED" or event["observed"] == "9":
        event.update(make_alert_ids(event))
    store = EvidenceStore()
    with pytest.raises(ValueError, match="WATCH_ALERT_NOT_TRIGGERED"):
        freeze_synthetic_watch_alert(event, evaluation_as_of=NOW,
                                     fixture=fixture(), store=store)
    assert store._versions == {}


@pytest.mark.parametrize("changes,code", [
    ({"synthetic_only": False}, "SYNTHETIC_FIXTURE_REQUIRED"),
    ({"source_membership_id": "other"}, "MEMBERSHIP_CORRELATION_MISMATCH"),
    ({"raw_ref": "claim:row1"}, "SELF_REPORTED_SOURCE_IS_NOT_RAW_PROOF"),
    ({"raw_digest": alert()["source_hash"]}, "SELF_REPORTED_SOURCE_IS_NOT_RAW_PROOF"),
    ({"raw_digest": "bad"}, "RAW_DIGEST_INVALID"),
    ({"source_id": ""}, "SOURCE_ID_INVALID"),
    ({"calendar_id": ""}, "CALENDAR_ID_INVALID"),
    ({"freeze_at": NOW + timedelta(seconds=1)}, "EVIDENCE_NOT_CURRENT"),
    ({"detected_at": NOW - timedelta(minutes=5)}, "TIME_ORDER_INVALID"),
    ({"valid_until": NOW}, "EVIDENCE_NOT_CURRENT"),
])
def test_missing_quality_mismatch_or_time_fails_without_freeze(changes: dict, code: str) -> None:
    store = EvidenceStore()
    with pytest.raises(ValueError, match=code):
        freeze_synthetic_watch_alert(alert(), evaluation_as_of=NOW,
                                     fixture=fixture(**changes), store=store)
    assert store._versions == {}


def test_owner_mismatch_and_receipts_are_rejected_before_freeze() -> None:
    store = EvidenceStore()
    with pytest.raises(ValueError, match="OWNER_CORRELATION_MISMATCH"):
        freeze_synthetic_watch_alert(alert(pool="B"), evaluation_as_of=NOW,
                                     fixture=fixture(tenant_or_owner_id="other"), store=store)
    event = alert()
    event["run_mode"] = "SIM_MARKET"
    event.update(make_alert_ids(event))
    with pytest.raises(ValueError, match="SHADOW_NO_SIDE_EFFECT_REQUIRED"):
        freeze_synthetic_watch_alert(event, evaluation_as_of=NOW,
                                     fixture=fixture(), store=store)
    assert store._versions == {}


def test_same_version_conflict_preserves_original_record() -> None:
    store = EvidenceStore()
    event = alert()
    first = freeze_synthetic_watch_alert(event, evaluation_as_of=NOW,
                                         fixture=fixture(), store=store)
    with pytest.raises(EvidenceConflict, match="EVIDENCE_CONFLICT"):
        freeze_synthetic_watch_alert(event, evaluation_as_of=NOW,
                                     fixture=fixture(raw_digest="d" * 64), store=store)
    assert store.get_exact(first) is first
    assert len(store._versions) == 1
    assert len(next(iter(store._versions.values()))) == 1


def test_duplicate_json_key_is_not_a_safe_mapping_input() -> None:
    event = alert()
    raw = json.dumps(event).replace('"pool": "A",', '"pool": "A", "pool": "B",')
    decision = load_alert_json(raw, evaluation_as_of=NOW)
    assert (decision.status, decision.code, decision.event) == (
        "UNJUDGEABLE", "DUPLICATE_JSON_KEY", None)
    store = EvidenceStore()
    # A raw JSON string must not be silently parsed by this mapping-only bridge.
    with pytest.raises(ValueError, match="WATCH_ALERT_NOT_TRIGGERED"):
        freeze_synthetic_watch_alert(raw, evaluation_as_of=NOW,
                                     fixture=fixture(), store=store)
    assert store._versions == {}
