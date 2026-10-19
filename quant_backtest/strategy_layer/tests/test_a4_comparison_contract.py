"""只用内存合成的 inspect 返回值，检验 A4 对账可比性边界。"""

import hashlib
import json
from copy import deepcopy

import pytest

from strategy_layer.a4_comparison_contract import evaluate_comparison


HASH = "a" * 64


def _digest(payload):
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def observation():
    value = {
        "trade_date": "20261016",
        "cn_detect_date": "2026-10-16",
        "cn_age_calendar_days": 0,
        "market_health": {"CN": {"detect_date": "2026-10-16", "rows": 1,
                                 "status": "FRESH", "age_calendar_days": 0}},
        "history": {"status": "MATCHED", "path": "picks.json", "sha256": HASH,
                    "scan_run_date": "2026-10-16",
                    "trade_basis": "2026-10-16 收盘", "chanlun_batch_date": "2026-10-16"},
        "chanlun_reconciliation": {"batch_status": "MATCHED",
                                   "source_difference_count": 0,
                                   "source_differences": {}},
        "source_files": [{"path": "cn.jsonl", "status": "OK", "sha256": HASH}],
        "source_anomalies": [], "selected_conversion_skips": [],
        "invalid_source_dates": {}, "duplicate_symbols": {},
        "contradictory_symbols": {}, "picks_only": [], "adapter_only": [],
        "input_snapshot_scope": "PROBE_ONLY_NOT_PRODUCTION",
        "issues": ["SIX_STRATEGY_CONTRACT_UNPROVEN", "PG_PERSISTENCE_UNPROVEN"],
        "verdict": "BLOCK_CUTOVER",
    }
    value["input_manifest"] = {
        "scope": "PROBE_OBSERVATIONS_NOT_GLOBAL_ATOMIC_OR_PRODUCTION",
        "pool_files": [dict(item) for item in value["source_files"]],
        "archive": {"path": value["history"]["path"], "status": value["history"]["status"],
                    "sha256": value["history"]["sha256"]},
    }
    value["input_manifest"]["sha256"] = _digest(
        {"pool_files": value["input_manifest"]["pool_files"],
         "archive": value["input_manifest"]["archive"]})
    return value


def test_matching_evidence_only_comparable_within_probe_and_never_cutover():
    value = observation()
    before = deepcopy(value)
    first = evaluate_comparison(value)
    assert first == evaluate_comparison(value)
    assert value == before
    assert first["archive_status"] == "MATCHED"
    assert first["cn_selected_batch"] == "PRESENT"
    assert first["cn_source_events"] == "MATCHED"
    assert first["cn_batch_alignment"] == "MATCHED"
    assert first["observation_comparability"] == "COMPARABLE_WITHIN_PROBE"
    assert first["reasons"] == []
    assert first["cutover_verdict"] == "BLOCK_CUTOVER"
    assert first["observation_scope"] == "PROBE_ONLY_NOT_PRODUCTION_ATOMIC_SNAPSHOT"


@pytest.mark.parametrize("raw,expected", [
    ("DATE_MISMATCH", "DATE_MISMATCH"),
    ("NOT_REQUESTED", "NOT_REQUESTED"),
    ("INVALID_ARCHIVE", "INVALID"),
    ("UNREADABLE", "INVALID"),
    ("unexpected", "INVALID"),
])
def test_archive_statuses_are_distinct_and_never_compared(raw, expected):
    value = observation()
    value["history"]["status"] = raw
    value["chanlun_reconciliation"] = None
    value["picks_only"] = value["adapter_only"] = None
    result = evaluate_comparison(value)
    assert result["archive_status"] == expected
    assert result["cn_source_events"] == "NOT_COMPARED"
    assert result["observation_comparability"] == "NOT_COMPARABLE"
    assert result["cutover_verdict"] == "BLOCK_CUTOVER"


def test_cn_selected_batch_absent_or_invalid_cannot_be_inferred():
    for health, expected in (({}, "ABSENT"),
                             ({"CN": {"rows": 0, "status": "FRESH"}}, "INVALID")):
        value = observation()
        value["market_health"] = health
        result = evaluate_comparison(value)
        assert result["cn_selected_batch"] == expected
        assert result["cn_source_events"] == "NOT_COMPARED"
        assert result["observation_comparability"] == "NOT_COMPARABLE"


@pytest.mark.parametrize("batch,differences,count,events,alignment", [
    ("MISMATCH", {}, 0, "MATCHED", "MISMATCH"),
    ("UNPROVEN", {}, 0, "MATCHED", "UNPROVEN"),
    ("MATCHED", {"000001.SZ": {"pool_only": [{"tag": "2买", "dt": "2026-10-15",
                                                      "count": 1}], "archive_only": []}},
     1, "MISMATCH", "MATCHED"),
    ("MATCHED", {}, 1, "INVALID_EVIDENCE", "MATCHED"),
])
def test_batch_and_source_events_are_independent(batch, differences, count, events, alignment):
    value = observation()
    value["chanlun_reconciliation"].update(batch_status=batch,
                                           source_differences=differences,
                                           source_difference_count=count)
    result = evaluate_comparison(value)
    assert result["cn_source_events"] == events
    assert result["cn_batch_alignment"] == alignment
    assert result["observation_comparability"] == "NOT_COMPARABLE"


def test_missing_source_path_is_not_missing_market_assertion():
    value = observation()
    value["source_files"].append({"path": "pool_us*.jsonl", "status": "MISSING",
                                  "sha256": None})
    result = evaluate_comparison(value)
    assert result["missing_source_paths"] == ["pool_us*.jsonl"]
    assert result["missing_market_inference"] == "NOT_INFERRED_FROM_MISSING_PATHS"
    assert "SOURCE_FILE_MISSING" in result["reasons"]
    assert result["observation_comparability"] == "NOT_COMPARABLE"


@pytest.mark.parametrize("field,value,reason", [
    ("duplicate_symbols", {"000001.SZ": 2}, "DUPLICATE_SYMBOLS_PRESENT_OR_INVALID"),
    ("contradictory_symbols", {"000001.SZ": {"actions": ["buy", "sell"]}},
     "CONTRADICTORY_SYMBOLS_PRESENT_OR_INVALID"),
    ("selected_conversion_skips", [{"reason": "UNKNOWN_TYPE_OR_INVALID_CODE"}],
     "SELECTED_CONVERSION_SKIPS_PRESENT_OR_INVALID"),
    ("source_anomalies", [{"reason": "BAD_JSON"}],
     "SOURCE_ANOMALIES_PRESENT_OR_INVALID"),
    ("picks_only", ["000002.SZ"], "PICKS_ONLY_PRESENT_OR_INVALID"),
    ("invalid_source_dates", {"CN": 1}, "INVALID_SOURCE_DATES_PRESENT_OR_INVALID"),
])
def test_anomalies_are_not_silently_resolved(field, value, reason):
    data = observation()
    data[field] = value
    result = evaluate_comparison(data)
    assert reason in result["reasons"]
    assert result["observation_comparability"] == "NOT_COMPARABLE"
    assert result["cutover_verdict"] == "BLOCK_CUTOVER"


def test_inconsistent_same_day_and_batch_evidence_fail_closed():
    for section, field, value in (
        ("history", "scan_run_date", "2026-10-15"),
        ("history", "trade_basis", "2026-10-15 收盘"),
        ("history", "chanlun_batch_date", "2026-10-15"),
        ("market_health", "CN", {"detect_date": "2026-10-15", "rows": 1,
                                  "status": "FRESH"}),
    ):
        data = observation()
        data[section][field] = value
        result = evaluate_comparison(data)
        assert result["observation_comparability"] == "NOT_COMPARABLE"
        assert result["cutover_verdict"] == "BLOCK_CUTOVER"


def test_unknown_input_or_probe_claims_fail_closed():
    assert evaluate_comparison(None)["observation_comparability"] == "NOT_COMPARABLE"
    for field, value in (("issues", []), ("verdict", "APPROVED"),
                         ("input_snapshot_scope", "PRODUCTION_ATOMIC"),
                         ("input_manifest", {"scope": "PRODUCTION_ATOMIC"}),
                         ("source_files", None),
                         ("market_health", {"CN": {"detect_date": "2026-10-16",
                                                    "rows": 1, "status": "FUTURE"}})):
        data = observation()
        data[field] = value
        result = evaluate_comparison(data)
        assert result["observation_comparability"] == "NOT_COMPARABLE"
        assert result["cutover_verdict"] == "BLOCK_CUTOVER"


def test_batch_age_and_reported_age_must_agree():
    value = observation()
    value["market_health"]["CN"]["age_calendar_days"] = 3
    result = evaluate_comparison(value)
    assert "MARKET_BATCH_DATE_AGE_INCONSISTENT" in result["reasons"]
    assert result["observation_comparability"] == "NOT_COMPARABLE"

    value = observation()
    value["market_health"]["CN"]["status"] = "FRESH"
    value["market_health"]["CN"]["detect_date"] = "2026-10-10"
    value["market_health"]["CN"]["age_calendar_days"] = 0
    result = evaluate_comparison(value)
    assert "MARKET_BATCH_DATE_AGE_INCONSISTENT" in result["reasons"]

    value = observation()
    value["market_health"]["CN"]["detect_date"] = "2026-10-20"
    value["market_health"]["CN"]["status"] = "FUTURE"
    value["market_health"]["CN"]["age_calendar_days"] = -4
    result = evaluate_comparison(value)
    # 年龄与状态映射自洽（-4→FUTURE），但未来批次仍按现有规则不得视为 FRESH。
    assert "MARKET_BATCH_DATE_AGE_INCONSISTENT" not in result["reasons"]
    assert "CN_BATCH_NOT_FRESH" in result["reasons"]


def test_top_level_cn_age_must_match_batch_evidence():
    value = observation()
    value["cn_age_calendar_days"] = 1
    result = evaluate_comparison(value)
    assert "CN_BATCH_AGE_INCONSISTENT" in result["reasons"]
    assert result["observation_comparability"] == "NOT_COMPARABLE"


def test_manifest_must_reflect_observation_and_digest():
    value = observation()
    value["input_manifest"]["sha256"] = "b" * 64
    result = evaluate_comparison(value)
    assert "OBSERVATION_MANIFEST_INCONSISTENT" in result["reasons"]
    assert result["observation_comparability"] == "NOT_COMPARABLE"

    value = observation()
    value["input_manifest"]["pool_files"] = []
    result = evaluate_comparison(value)
    assert "OBSERVATION_MANIFEST_INCONSISTENT" in result["reasons"]

    value = observation()
    value["input_manifest"]["archive"] = {"path": "other.json", "status": "MATCHED",
                                          "sha256": HASH}
    result = evaluate_comparison(value)
    assert "OBSERVATION_MANIFEST_INCONSISTENT" in result["reasons"]
