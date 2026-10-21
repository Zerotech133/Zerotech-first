"""影子比较预检仅使用内存合成证据，不访问生产入口。"""

from copy import deepcopy

import pytest

from strategy_layer.shadow_comparison_gate import preflight_shadow_comparison


HASH = "a" * 64


def evidence():
    common = {
        "trade_date": "20261019", "market": "CN", "stage": "approved_candidate",
        "snapshot_id": "frozen-batch", "sha256": HASH, "snapshot_verified": True,
        "hash_verified": True, "pit_evidence_version": "pit-v1", "pit_verified": True,
        "pit_available_at": "2026-10-19T14:00:00+08:00",
        "pit_cutoff_at": "2026-10-19T15:00:00+08:00",
    }
    old = {**common, "consumer_version": "reader-v1",
           "consumer_read_at": "2026-10-19T15:01:00+08:00",
           "consumer_receipt": "receipt-1", "consumer_receipt_verified": True}
    new = dict(common)
    mapping = {"approved": True, "old_stage": "approved_candidate",
               "new_stage": "approved_candidate"}
    isolation = {"research_identity": "isolated-worker",
                 "independent_output": "research-only",
                 "research_only": True, "production_pg_write_unreachable": True,
                 "production_container_write_unreachable": True}
    return old, new, mapping, isolation


def check(items):
    return preflight_shadow_comparison(*items)


def blocked(items, reason):
    result = check(items)
    assert result["comparability"] == "NOT_COMPARABLE"
    assert result["comparability_basis"] == "CALLER_DECLARATIONS_ONLY"
    assert result["external_evidence_verification"] == "NOT_PERFORMED"
    assert result["cutover_verdict"] == "BLOCK_CUTOVER"
    assert reason in result["reasons"]


def test_valid_sha256_hex_digits_are_accepted():
    items = evidence()
    digest = "0123456789abcdef" * 4
    items[0]["sha256"] = digest
    items[1]["sha256"] = digest
    assert check(items)["comparability"] == "COMPARABLE_CANDIDATE"


def test_complete_evidence_is_only_candidate_and_does_not_mutate_input():
    items = evidence()
    before = deepcopy(items)
    assert check(items) == {"comparability": "COMPARABLE_CANDIDATE",
                            "comparability_basis": "CALLER_DECLARATIONS_ONLY",
                            "external_evidence_verification": "NOT_PERFORMED",
                            "cutover_verdict": "BLOCK_CUTOVER", "reasons": []}
    assert items == before
    assert not any("return" in key or "jaccard" in key for key in check(items))


def test_caller_claims_cannot_upgrade_external_verification_or_cutover():
    items = evidence()
    items[0]["external_evidence_verification"] = "VERIFIED"
    items[1]["externally_approved"] = True
    items[2]["external_evidence_verification"] = "VERIFIED"
    items[3]["cutover_verdict"] = "ALLOW_CUTOVER"
    result = check(items)
    assert result["comparability"] == "COMPARABLE_CANDIDATE"
    assert result["comparability_basis"] == "CALLER_DECLARATIONS_ONLY"
    assert result["external_evidence_verification"] == "NOT_PERFORMED"
    assert result["cutover_verdict"] == "BLOCK_CUTOVER"


@pytest.mark.parametrize("side", [0, 1])
def test_missing_side_blocks(side):
    items = list(evidence())
    items[side] = None
    blocked(items, ("OLD" if side == 0 else "NEW") + "_EVIDENCE_INVALID")


@pytest.mark.parametrize("value", [None, 20261019, "20261319", "20260230", "2026-10-19"])
def test_invalid_or_missing_trade_date_blocks(value):
    items = list(evidence())
    items[0]["trade_date"] = value
    blocked(items, "OLD_TRADE_DATE_INVALID")


def test_cross_day_blocks():
    items = list(evidence())
    items[1]["trade_date"] = "20261018"
    blocked(items, "TRADE_DATE_MISMATCH")


def test_cross_market_blocks():
    items = list(evidence())
    items[1]["market"] = "HK"
    blocked(items, "MARKET_MISMATCH")


def test_cross_stage_blocks_even_with_explicit_approved_mapping():
    items = list(evidence())
    items[1]["stage"] = items[2]["new_stage"] = "buy_only"
    blocked(items, "STAGE_MISMATCH")


@pytest.mark.parametrize("mapping", [None, {}, {"approved": 1, "old_stage": "approved_candidate",
                                                 "new_stage": "approved_candidate"},
                                    {"approved": True, "old_stage": "buy_only",
                                     "new_stage": "approved_candidate"}])
def test_unapproved_missing_or_mismatched_mapping_blocks(mapping):
    items = list(evidence())
    items[2] = mapping
    blocked(items, "STAGE_MAPPING_UNAPPROVED_OR_MISMATCHED")


def test_self_declared_mapping_does_not_override_unproven_evidence():
    items = list(evidence())
    items[0]["pit_verified"] = False
    blocked(items, "OLD_PIT_VERIFIED_UNPROVEN")


def test_self_declared_mapping_cannot_approve_cross_stage_comparison():
    items = list(evidence())
    items[1]["stage"] = items[2]["new_stage"] = "buy_only"
    blocked(items, "STAGE_MISMATCH")


@pytest.mark.parametrize("field,value,reason", [
    ("snapshot_id", None, "OLD_SNAPSHOT_ID_MISSING"),
    ("sha256", "wrong", "OLD_HASH_INVALID"),
    ("snapshot_verified", 1, "OLD_SNAPSHOT_VERIFIED_UNPROVEN"),
    ("hash_verified", False, "OLD_HASH_VERIFIED_UNPROVEN"),
    ("pit_verified", None, "OLD_PIT_VERIFIED_UNPROVEN"),
    ("pit_available_at", "2026-10-19T16:00:00+08:00", "OLD_PIT_CUTOFF_UNPROVEN"),
    ("pit_cutoff_at", "2026-10-19T15:00:00", "OLD_PIT_CUTOFF_UNPROVEN"),
    ("pit_evidence_version", "", "OLD_PIT_EVIDENCE_VERSION_MISSING"),
])
def test_snapshot_hash_pit_evidence_must_be_present_and_typed(field, value, reason):
    items = list(evidence())
    items[0][field] = value
    blocked(items, reason)


def test_cross_timezone_available_after_cutoff_blocks_by_instant():
    items = list(evidence())
    items[0]["pit_available_at"] = "2026-10-19T08:30:00+01:00"
    blocked(items, "OLD_PIT_CUTOFF_UNPROVEN")


def test_equal_instant_cutoffs_with_different_offsets_are_comparable():
    items = list(evidence())
    items[1]["pit_cutoff_at"] = "2026-10-19T03:00:00-04:00"
    assert check(items) == {"comparability": "COMPARABLE_CANDIDATE",
                            "comparability_basis": "CALLER_DECLARATIONS_ONLY",
                            "external_evidence_verification": "NOT_PERFORMED",
                            "cutover_verdict": "BLOCK_CUTOVER", "reasons": []}


def test_same_local_time_with_different_offsets_is_not_same_cutoff():
    items = list(evidence())
    items[1]["pit_cutoff_at"] = "2026-10-19T15:00:00+07:00"
    blocked(items, "PIT_CUTOFF_MISMATCH")


def test_equal_instant_cutoff_on_different_local_dates_blocks():
    items = list(evidence())
    items[1]["pit_cutoff_at"] = "2026-10-18T23:00:00-08:00"
    blocked(items, "NEW_PIT_CUTOFF_UNPROVEN")


def test_old_consumer_next_day_in_cutoff_timezone_blocks():
    items = list(evidence())
    items[0]["consumer_read_at"] = "2026-10-19T08:01:00-08:00"
    blocked(items, "OLD_CONSUMER_READ_INVALID")


def test_old_consumer_next_local_day_but_trade_day_in_cutoff_timezone_passes():
    items = list(evidence())
    items[0]["consumer_read_at"] = "2026-10-20T00:30:00+09:00"
    assert check(items) == {"comparability": "COMPARABLE_CANDIDATE",
                            "comparability_basis": "CALLER_DECLARATIONS_ONLY",
                            "external_evidence_verification": "NOT_PERFORMED",
                            "cutover_verdict": "BLOCK_CUTOVER", "reasons": []}


def test_pit_cutoff_and_version_must_match():
    items = list(evidence())
    items[1]["pit_cutoff_at"] = "2026-10-19T14:30:00+08:00"
    blocked(items, "PIT_CUTOFF_MISMATCH")
    items = list(evidence())
    items[1]["pit_evidence_version"] = "pit-v2"
    blocked(items, "PIT_EVIDENCE_VERSION_MISMATCH")


@pytest.mark.parametrize("field,value,reason", [
    ("consumer_version", None, "OLD_CONSUMER_VERSION_MISSING"),
    ("consumer_read_at", "2026-10-18T15:01:00+08:00", "OLD_CONSUMER_READ_INVALID"),
    ("consumer_read_at", "2026-10-20T15:01:00+08:00", "OLD_CONSUMER_READ_INVALID"),
    ("consumer_receipt", "", "OLD_CONSUMER_RECEIPT_MISSING"),
    ("consumer_receipt_verified", 1, "OLD_CONSUMER_RECEIPT_UNPROVEN"),
])
def test_old_consumer_version_time_and_receipt_must_be_proven(field, value, reason):
    items = list(evidence())
    items[0][field] = value
    blocked(items, reason)


@pytest.mark.parametrize("field,value", [
    ("research_identity", None), ("independent_output", ""),
    ("research_only", False), ("production_pg_write_unreachable", False),
    ("production_container_write_unreachable", 1),
])
def test_false_isolation_or_invalid_identity_blocks(field, value):
    items = list(evidence())
    items[3][field] = value
    blocked(items, "RESEARCH_ISOLATION_UNPROVEN")


def test_missing_isolation_blocks():
    items = list(evidence())
    items[3] = None
    blocked(items, "RESEARCH_ISOLATION_UNPROVEN")
