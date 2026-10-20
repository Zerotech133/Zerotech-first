"""WO-QT-20261020-036：只用合成 dict 检查 R0 逐段证据对账。"""
from copy import deepcopy
import hashlib
import json

import pytest

from strategy_layer.r0_evidence_reconciliation import reconcile_evidence
from strategy_layer.r0_readonly_e2e import inspect_batch
from strategy_layer.r0_research_brief import build_research_brief


DAY = "20261019"
HASH = "a" * 64


def evidence():
    rows = [
        {"trade_date": DAY, "symbol": "002633.SZ", "action": "sell",
         "contributions": {"chan_monitor": {"superseded": [{"action": "buy"}]}}},
        {"trade_date": DAY, "symbol": "002634.SZ", "action": "buy",
         "contributions": {"chan_monitor": {}}},
    ]
    counts = {"source_events": 3, "signals": 3, "kept": 2,
              "combined": 2, "pool": 1}
    probe = {
        "scope": "RESEARCH_ONLY_NOT_PRODUCTION", "cutover_verdict": "BLOCK_CUTOVER",
        "real_source_verdict": "BLOCKED_REAL_DATA", "trade_date": DAY,
        "status": "UNVERIFIED", "chain_status": "VERIFIED_IN_MEMORY",
        "pool_status": "IN_MEMORY_ONLY", "failures": [],
        "source": {"business_date": DAY, "sha256": HASH,
                   "identity_status": "UNVERIFIED"},
        "counts": counts, "combined": rows,
        "pool": {"scope": "RESEARCH_ONLY_NOT_PRODUCTION",
                 "cutover_verdict": "BLOCK_CUTOVER", "trade_date": DAY,
                 "stats": {"combined_total": 2, "buy_total": 1,
                           "superseded_total": 1},
                 "pool": [{"symbol": "002634.SZ", "action": "buy"}]},
    }
    brief = {
        "scope": "RESEARCH_ONLY_NOT_PRODUCTION", "cutover_verdict": "BLOCK_CUTOVER",
        "trade_date": DAY, "source": {"sha256": HASH, "business_date": DAY},
        "chain_status": "VERIFIED_IN_MEMORY", "counts": {
            **counts, "matched_events": None, "brief": None, "receipt": None},
        "scheme_c": {"contributions": 2, "superseded": 1},
        "pool_summary": {"buy_total": 1},
    }
    attestation = {"status": "VERIFIED", "identity_status": "VERIFIED",
                   "business_date": DAY, "source_sha256": HASH,
                   "event_count": 3, "historical_single_day_frozen": False,
                   "real_source_verdict": "BLOCKED_REAL_DATA"}
    receipt = {"status": "VERIFIED_RECEIPT_ONLY"}
    return probe, attestation, brief, receipt


def assert_blocked(result):
    assert result["real_source_verdict"] == "BLOCKED_REAL_DATA"
    assert result["cutover_verdict"] == "BLOCK_CUTOVER"
    assert result["status"] != "READY"


def test_full_in_memory_chain_with_local_verifications_no_ready():
    items = evidence()
    originals = deepcopy(items)
    result = reconcile_evidence(DAY, *items)
    assert result["status"] == "NOT_VERIFIED"
    assert [result["stages"][s]["status"] for s in
            ("source", "signal", "combined", "pool", "brief", "receipt")] == [
                "CALLER_ATTESTATION_CLAIM", "IN_MEMORY_ONLY", "IN_MEMORY_ONLY",
                "IN_MEMORY_ONLY", "IN_MEMORY_ONLY", "CALLER_RECEIPT_CLAIM"]
    assert result["counts"] == {"source_events": 3, "matched_events": None,
                                "signals": 3, "kept": 2, "combined": 2,
                                "pool": 1, "brief": 1, "receipt": 1}
    assert items == originals
    assert_blocked(result)


@pytest.mark.parametrize("index,path,value,stage", [
    (0, ("trade_date",), "20261018", "source"),
    (0, ("source", "business_date"), "20261018", "source"),
    (0, ("combined", 0, "trade_date"), "20261018", "combined"),
    (0, ("pool", "trade_date"), "20261018", "pool"),
    (1, ("business_date",), "20261018", "source"),
    (2, ("trade_date",), "20261018", "brief"),
    (2, ("source", "business_date"), "20261018", "brief"),
])
def test_wrong_day_rejected(index, path, value, stage):
    items = list(evidence())
    target = items[index]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    result = reconcile_evidence(DAY, *items)
    assert result["status"] == "REJECTED"
    assert result["stages"][stage]["status"] == "REJECTED"
    assert_blocked(result)


@pytest.mark.parametrize("index,path,value,stage", [
    (0, ("counts", "source_events"), 4, "source"),
    (0, ("counts", "signals"), 2, "signal"),
    (0, ("counts", "signals"), True, "signal"),
    (0, ("counts", "kept"), 3, "combined"),
    (0, ("counts", "combined"), 1, "combined"),
    (0, ("counts", "pool"), 2, "pool"),
    (0, ("combined", 0, "contributions"), {}, "combined"),
    (0, ("pool", "stats", "superseded_total"), 0, "pool"),
    (2, ("counts", "signals"), 2, "brief"),
    (2, ("scheme_c", "superseded"), 0, "brief"),
])
def test_counts_and_missing_segments_fail_closed(index, path, value, stage):
    items = list(evidence())
    target = items[index]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    result = reconcile_evidence(DAY, *items)
    assert result["stages"][stage]["status"] == "REJECTED"
    assert_blocked(result)


def test_missing_required_segment_and_pool_action_rejected():
    probe, att, brief, receipt = evidence()
    del probe["counts"]["signals"]
    assert reconcile_evidence(DAY, probe, att, brief, receipt)["stages"]["signal"]["status"] == "REJECTED"
    probe, att, brief, receipt = evidence()
    del probe["pool"]
    assert reconcile_evidence(DAY, probe, att, brief, receipt)["stages"]["pool"]["status"] == "REJECTED"
    probe, att, brief, receipt = evidence()
    probe["pool"]["pool"][0]["action"] = "sell"
    assert reconcile_evidence(DAY, probe, att, brief, receipt)["stages"]["pool"]["status"] == "REJECTED"


def test_no_file_network_database_or_replay_access(monkeypatch):
    import builtins
    import socket
    import sqlite3

    def forbidden(*_args, **_kwargs):
        pytest.fail("对账不允许外部访问")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    assert reconcile_evidence(DAY, *evidence())["stages"]["receipt"]["status"] == "CALLER_RECEIPT_CLAIM"


def test_receipt_without_matching_brief_cannot_be_verified():
    probe, att, brief, receipt = evidence()
    result = reconcile_evidence(DAY, probe, att, None, receipt)
    assert result["stages"]["brief"]["status"] == "NOT_VERIFIED"
    assert result["stages"]["receipt"]["status"] == "NOT_VERIFIED"
    assert result["counts"]["receipt"] is None
    assert_blocked(result)


def test_missing_and_partial_failure_never_become_zero():
    probe, att, brief, receipt = evidence()
    assert reconcile_evidence(DAY)["counts"]["source_events"] is None
    assert reconcile_evidence(DAY, probe)["stages"]["brief"]["status"] == "NOT_VERIFIED"
    probe["status"] = "ADAPTER_ERROR"
    probe["failures"] = [{"stage": "adapter", "reason": "partial"}]
    result = reconcile_evidence(DAY, probe, att, brief, receipt)
    assert result["stages"]["source"]["status"] == "REJECTED"
    assert all(result["counts"][key] is None for key in
               ("signals", "kept", "combined", "pool", "brief", "receipt"))
    assert_blocked(result)


def test_self_declared_empty_and_synthetic_receipt_not_trusted_zero():
    probe, att, brief, receipt = evidence()
    probe["counts"] = {"source_events": 0, "signals": 0, "kept": 0,
                       "combined": 0, "pool": 0}
    probe["chain_status"] = "SELF_DECLARED_EMPTY"
    result = reconcile_evidence(DAY, probe, None, brief, receipt)
    assert result["stages"]["signal"]["status"] == "NOT_VERIFIED"
    assert result["counts"]["signals"] is None
    assert result["counts"]["receipt"] is None
    assert_blocked(result)
    probe, att, brief, receipt = evidence()
    receipt["status"] = "NOT_VERIFIED"  # 合成/自回执须由上游核验拒绝
    result = reconcile_evidence(DAY, probe, att, brief, receipt)
    assert result["stages"]["receipt"]["status"] == "NOT_VERIFIED"
    assert result["counts"]["receipt"] is None
    assert_blocked(result)


def test_historical_multiday_counts_and_unmatched():
    probe, _, brief, _ = evidence()
    probe["counts"]["source_events"] = 9
    probe["counts"]["matched_events"] = 3
    brief["counts"]["source_events"] = 9
    brief["counts"]["matched_events"] = 3
    result = reconcile_evidence(DAY, probe, None, brief)
    assert result["counts"]["source_events"] == 9
    assert result["counts"]["matched_events"] == result["counts"]["signals"] == 3
    assert result["stages"]["source"]["count"] == 9
    assert_blocked(result)
    att = evidence()[1]
    assert reconcile_evidence(DAY, probe, att)["stages"]["source"]["status"] == "REJECTED"
    probe["status"] = "NO_MATCH_UNVERIFIED"
    probe["chain_status"] = "POST_HOC_NO_MATCH"
    probe["counts"].update(matched_events=None, signals=None, kept=None,
                           combined=None, pool=None)
    result = reconcile_evidence(DAY, probe)
    assert result["counts"]["source_events"] == 9
    assert result["counts"]["matched_events"] is None
    assert result["counts"]["signals"] is None
    assert_blocked(result)


@pytest.mark.parametrize("index,status", [(0, "READY"), (1, "READY"), (3, "READY")])
def test_self_reported_ready_is_not_authority(index, status):
    items = list(evidence())
    items[index]["status"] = status
    result = reconcile_evidence(DAY, *items)
    assert result["status"] == "REJECTED"
    assert_blocked(result)


@pytest.mark.parametrize("key,value", [
    ("identity_status", "VERIFIED"),
    ("identity_status", "READY"),
])
def test_probe_identity_cannot_self_upgrade(key, value):
    probe, att, brief, receipt = evidence()
    probe["source"][key] = value
    assert reconcile_evidence(DAY, probe, att, brief, receipt)["status"] == "REJECTED"


def test_unhashable_pool_symbol_is_rejected_without_exception():
    probe, att, brief, receipt = evidence()
    probe["pool"]["pool"][0]["symbol"] = ["002634.SZ"]
    result = reconcile_evidence(DAY, probe, att, brief, receipt)
    assert result["stages"]["pool"]["status"] == "REJECTED"
    assert_blocked(result)


def test_source_claim_frozen_true_is_rejected():
    probe, att, brief, receipt = evidence()
    att["historical_single_day_frozen"] = True
    assert reconcile_evidence(DAY, probe, att, brief, receipt)["stages"]["source"]["status"] == "REJECTED"


def test_missing_failure_list_and_wrong_empty_mode_rejected():
    probe, att, brief, receipt = evidence()
    del probe["failures"]
    assert reconcile_evidence(DAY, probe, att, brief, receipt)["status"] == "REJECTED"
    probe, att, brief, receipt = evidence()
    probe["counts"]["matched_events"] = 3
    probe["chain_status"] = "SELF_DECLARED_EMPTY"
    assert reconcile_evidence(DAY, probe)["status"] == "REJECTED"


def test_real_probe_and_brief_shape_with_synthetic_batch(tmp_path):
    source = tmp_path / "synthetic.jsonl"
    row = {"code": "002633.SZ", "type": "1买", "dt": "2026-10-19",
           "detect_date": "2026-10-19", "price": 10}
    raw = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
    source.write_bytes(raw)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "source_path": str(source.resolve()), "business_date": DAY,
        "observed_date": "2026-10-20", "source_sha256": hashlib.sha256(raw).hexdigest(),
        "event_count": 1,
    }), encoding="utf-8")
    probe = inspect_batch(DAY, source, manifest)
    assert probe["chain_status"] == "VERIFIED_IN_MEMORY"
    brief = build_research_brief(probe)
    result = reconcile_evidence(DAY, probe, brief=brief)
    assert result["status"] == "NOT_VERIFIED"
    assert result["stages"]["brief"]["status"] == "IN_MEMORY_ONLY"
    assert result["counts"]["pool"] == 1
    assert_blocked(result)


@pytest.mark.parametrize("bad", ["20260230", "2026-10-19", None])
def test_invalid_requested_day(bad):
    with pytest.raises(ValueError):
        reconcile_evidence(bad)
