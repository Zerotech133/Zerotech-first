"""Isolated fail-closed WATCH1 per-security contract tests; no external services."""

import copy
import hashlib
import json
from datetime import datetime, timezone

import pytest

from quant_backtest.watch_alert_contract import (
    load_alert_json,
    make_alert_ids,
    validate_alert,
)


NOW = datetime(2026, 10, 19, 10, 5, tzinfo=timezone.utc)
EMPTY_RECEIPT = {"status": "NOT_ATTEMPTED", "receipt_id": None, "at": None}


def claim(pool="A", mode="SHADOW", observed="12"):
    """Build an individually identifiable, offline synthetic event."""
    record = {
        "schema_version": "WATCH1_V1", "alert_id": "", "trace_id": "",
        "dedupe_key": "", "pool": pool, "run_mode": mode,
        "market": "SSE", "code": "600519", "instrument_type": "STOCK",
        "strategy": "trend" if pool == "A" else None,
        "strategy_version": "1" if pool == "A" else None,
        "source_batch": "batch-1" if pool == "A" else None,
        "owner": "owner-1" if pool == "B" else None,
        "watch_rule": "price-break" if pool == "B" else None,
        "watch_rule_version": "1" if pool == "B" else None,
        "membership_event": {"event_id": "episode-1", "kind": "ENTER",
                             "as_of": "2026-10-19T09:00:00Z", "market": "SSE",
                             "code": "600519", "pool": pool,
                             "qualification": ({"strategy": "trend", "strategy_version": "1",
                                                "source_batch": "batch-1"} if pool == "A" else
                                               {"owner": "owner-1", "watch_rule": "price-break",
                                                "watch_rule_version": "1"})},
        "rule": "above-10", "comparator": "GT", "threshold": "10",
        "observed": observed, "quote_as_of": "2026-10-19T10:00:00Z",
        "visible_at": "2026-10-19T10:01:00Z", "source_hash": "a" * 64,
        "source_ref": "raw:fixture:row-1", "source_snapshot": None,
        "source_kind": "DIRECT_QUOTE", "quality": "VERIFIED",
        "facts": ["quote:row-1"], "hypotheses": ["possible breakout"],
        "supporting_refs": ["quote:row-1"], "contradicting_refs": [],
        "evidence_assessment": "SUPPORTED",
        "notification": copy.deepcopy(EMPTY_RECEIPT),
        "sim_handoff": copy.deepcopy(EMPTY_RECEIPT),
    }
    source_fields = ("source_ref", "source_kind", "market", "code",
                     "instrument_type", "observed", "quote_as_of", "visible_at", "quality")
    record["source_snapshot"] = {key: record[key] for key in source_fields}
    record["source_hash"] = hashlib.sha256(json.dumps(
        record["source_snapshot"], ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    record.update(make_alert_ids(record))
    return record


@pytest.mark.parametrize("pool,mode", [("A", "SHADOW"), ("B", "SHADOW"),
                                      ("A", "SIM_MARKET"), ("B", "SIM_MARKET")])
def test_valid_round_trip_and_stable_identity(pool, mode):
    record = claim(pool, mode)
    outcome = load_alert_json(json.dumps(record), evaluation_as_of=NOW)
    assert outcome.status == "TRIGGERED"
    assert outcome.code == "OK"
    assert json.loads(json.dumps(outcome.to_dict()))["event"] == record
    assert make_alert_ids(record) == make_alert_ids(record)
    assert claim("A")["dedupe_key"] != claim("B")["dedupe_key"]
    assert claim("A", "SHADOW")["dedupe_key"] != claim("A", "SIM_MARKET")["dedupe_key"]


def test_only_proven_negative_is_not_triggered():
    record = claim(observed="10")
    assert validate_alert(record, evaluation_as_of=NOW).status == "NOT_TRIGGERED"
    record["quality"] = "UNKNOWN"
    assert validate_alert(record, evaluation_as_of=NOW).status == "UNJUDGEABLE"


@pytest.mark.parametrize("change,code", [
    (lambda r: r.pop("source_hash"), "SCHEMA_FIELDS"),
    (lambda r: r.update(pool="C"), "POOL"),
    (lambda r: r.update(run_mode="LIVE"), "RUN_MODE"),
    (lambda r: r.update(owner="other"), "POOL_A_QUALIFICATION"),
    (lambda r: r.update(source_batch=None), "POOL_A_QUALIFICATION"),
    (lambda r: r["membership_event"].update(kind="EXIT"), "MEMBERSHIP_INACTIVE"),
    (lambda r: r["membership_event"].update(code="000001"), "MEMBERSHIP_IDENTITY"),
    (lambda r: r["membership_event"].update(pool="B"), "MEMBERSHIP_IDENTITY"),
    (lambda r: r["membership_event"]["qualification"].update(source_batch="batch-2"),
     "MEMBERSHIP_IDENTITY"),
    (lambda r: r["membership_event"].update(as_of="2026-10-19T09:00:00"), "MEMBERSHIP_TIMEZONE"),
    (lambda r: r.update(quote_as_of="2026-10-19T10:00:00+08:00"), "QUOTE_TIMEZONE"),
    (lambda r: r.update(visible_at="2026-10-19T11:00:00Z"), "FUTURE_OR_ORDERED_TIME"),
    (lambda r: r.update(source_hash="x" * 64), "SOURCE_HASH"),
    (lambda r: r.update(source_kind="DISPLAY_SUMMARY"), "SUMMARY_NOT_EVIDENCE"),
    (lambda r: r.update(quality="STALE"), "QUOTE_QUALITY"),
    (lambda r: r.update(facts=[]), "EVIDENCE_FACTS"),
    (lambda r: r.update(supporting_refs=[]), "EVIDENCE_SUPPORTING_REFS"),
    (lambda r: r.update(contradicting_refs=["quote:row-1"]), "EVIDENCE_CONFLICT"),
    (lambda r: r.update(source_ref="raw:other:row-1"), "SOURCE_SNAPSHOT_MISMATCH"),
    (lambda r: r.update(source_ref="../raw-file"), "SOURCE_REF"),
    (lambda r: r.update(source_ref="C:\\temp\\raw"), "SOURCE_REF"),
    (lambda r: r.update(source_ref="https://example.test/row"), "SOURCE_REF"),
    (lambda r: r["source_snapshot"].update(code="000001"), "SOURCE_SNAPSHOT_MISMATCH"),
    (lambda r: r.update(source_hash="b" * 64), "SOURCE_SNAPSHOT_HASH"),
    (lambda r: r.update(observed="NaN"), "OBSERVED"),
    (lambda r: r.update(alert_id="b" * 64), "IDENTITY_DIGEST"),
    (lambda r: r.update(notification={"status": "RECORDED", "receipt_id": None,
                                       "at": "2026-10-19T10:02:00Z"}), "NOTIFICATION_RECEIPT"),
    (lambda r: r.update(notification={"status": "RECORDED", "receipt_id": "n1",
                                       "at": "2026-10-19T10:02:00Z"}), "SHADOW_SIDE_EFFECT"),
])
def test_unsafe_claim_fails_closed(change, code):
    record = claim()
    change(record)
    outcome = validate_alert(record, evaluation_as_of=NOW)
    assert (outcome.status, outcome.code, outcome.event) == ("UNJUDGEABLE", code, None)


def test_handoff_receipt_sim_only_and_nontriggered_receipts_forbidden():
    record = claim(mode="SIM_MARKET")
    record["sim_handoff"] = {"status": "RECORDED", "receipt_id": "handoff-1",
                             "at": "2026-10-19T10:02:00Z"}
    assert validate_alert(record, evaluation_as_of=NOW).status == "TRIGGERED"
    negative = claim(mode="SIM_MARKET", observed="9")
    negative["notification"] = {"status": "RECORDED", "receipt_id": "send-1",
                                 "at": "2026-10-19T10:02:00Z"}
    assert validate_alert(negative, evaluation_as_of=NOW).code == "NON_TRIGGERED_RECEIPT"


def test_missing_membership_display_summary_and_cross_instrument_dedup():
    record = claim()
    other = claim()
    other["code"] = "600520"
    other["membership_event"]["code"] = "600520"
    assert make_alert_ids(record)["dedupe_key"] != make_alert_ids(other)["dedupe_key"]
    record["membership_event"] = None
    assert validate_alert(record, evaluation_as_of=NOW).code == "MEMBERSHIP_SCHEMA"
    summary = claim()
    summary["source_kind"] = "DISPLAY_SUMMARY"
    summary.update(make_alert_ids(summary))
    assert validate_alert(summary, evaluation_as_of=NOW).code == "SUMMARY_NOT_EVIDENCE"


def test_cross_domain_receipts_cannot_reuse_identifier():
    record = claim(mode="SIM_MARKET")
    receipt = {"status": "RECORDED", "receipt_id": "shared-1",
               "at": "2026-10-19T10:02:00Z"}
    record["notification"] = copy.deepcopy(receipt)
    record["sim_handoff"] = copy.deepcopy(receipt)
    assert validate_alert(record, evaluation_as_of=NOW).code == "CROSS_DOMAIN_RECEIPT_ID"
    record["sim_handoff"]["receipt_id"] = "handoff-1"
    assert validate_alert(record, evaluation_as_of=NOW).status == "TRIGGERED"


def test_contested_evidence_retained_without_action_or_false_negative():
    record = claim()
    record["facts"].append("risk:row-2")
    record["contradicting_refs"] = ["risk:row-2"]
    record["evidence_assessment"] = "CONTESTED"
    record.update(make_alert_ids(record))
    result = validate_alert(record, evaluation_as_of=NOW)
    assert (result.status, result.code, result.event) == (
        "UNJUDGEABLE", "EVIDENCE_CONTESTED", None)
    diagnostic = result.to_dict()["diagnostic"]
    assert diagnostic["contradicting_refs"] == ["risk:row-2"]
    assert "membership_event" not in diagnostic
    assert "notification" not in diagnostic
    assert "source_snapshot" not in diagnostic
    record["observed"] = "9"
    record["source_snapshot"]["observed"] = "9"
    record["source_hash"] = hashlib.sha256(json.dumps(
        record["source_snapshot"], ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    record.update(make_alert_ids(record))
    negative = validate_alert(record, evaluation_as_of=NOW)
    assert negative.status == "NOT_TRIGGERED"
    assert negative.event["contradicting_refs"] == ["risk:row-2"]


def test_contested_recorded_side_effect_is_rejected():
    record = claim(mode="SIM_MARKET")
    record["facts"].append("risk:row-2")
    record["contradicting_refs"] = ["risk:row-2"]
    record["evidence_assessment"] = "CONTESTED"
    record["notification"] = {"status": "RECORDED", "receipt_id": "n1",
                              "at": "2026-10-19T10:02:00Z"}
    record.update(make_alert_ids(record))
    assert validate_alert(record, evaluation_as_of=NOW).code == "CONTESTED_RECEIPT"


def test_json_duplicate_keys_and_invalid_clock_rejected():
    raw = json.dumps(claim())
    duplicated = raw.replace('"pool": "A",', '"pool": "A", "pool": "B",')
    assert load_alert_json(duplicated, evaluation_as_of=NOW).code == "DUPLICATE_JSON_KEY"
    assert load_alert_json(raw, evaluation_as_of=datetime(2026, 10, 19)).code == "EVALUATION_TIME"
    assert load_alert_json("{}", evaluation_as_of=NOW).code == "SCHEMA_FIELDS"
