"""WO-QT-20261020-036：只用合成 dict 检查 R0 逐段证据对账。"""
from copy import deepcopy
import hashlib
import json

import pytest

from strategy_layer.r0_evidence_reconciliation import reconcile_evidence
from strategy_layer.r0_historical_pool_probe import inspect_historical_pool
from strategy_layer.r0_readonly_e2e import inspect_batch
from strategy_layer.r0_research_brief import build_research_brief


DAY = "20261019"
HASH = "a" * 64


def _superseded(action="buy", score=87.0, dt="2026-10-18", rationale="旧信号"):
    return {"action": action, "score": score, "dt": dt, "rationale": rationale}


def evidence():
    rows = [
        {"trade_date": DAY, "symbol": "002633.SZ", "action": "sell",
         "contributions": {"chan_monitor": {"superseded": [_superseded()]}}},
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
    assert result["status"] in ("NOT_VERIFIED", "REJECTED")


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


@pytest.mark.parametrize("index,path,value,stage", [
    (0, ("brief_status",), "VERIFIED", "source"),
    (0, ("receipt_status",), "VERIFIED_RECEIPT_ONLY", "source"),
    (1, ("source_sha256",), "b" * 64, "source"),
    (1, ("identity_status",), "UNVERIFIED", "source"),
    (1, ("event_count",), 4, "source"),
    (1, ("status",), "REJECTED", "source"),
    (2, ("counts", "brief"), 1, "brief"),
    (2, ("counts", "receipt"), 1, "brief"),
    (2, ("pool_summary", "buy_total"), 2, "brief"),
    (2, ("probe_status",), "VERIFIED", "brief"),
])
def test_bound_evidence_fields_must_match(index, path, value, stage):
    """验签摘要/身份/事件数、探针 brief|receipt 状态、简报边界均须逐项绑定。"""
    items = list(evidence())
    target = items[index]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    result = reconcile_evidence(DAY, *items)
    assert result["stages"][stage]["status"] == "REJECTED"
    assert_blocked(result)


@pytest.mark.parametrize("key", ["matched_events", "pool", "source_events"])
def test_brief_missing_count_key_is_rejected(key):
    items = list(evidence())
    del items[2]["counts"][key]
    result = reconcile_evidence(DAY, *items)
    assert result["stages"]["brief"]["status"] == "REJECTED"
    assert_blocked(result)


@pytest.mark.parametrize("path,value", [
    (("counts", "pool"), True),
    (("counts", "source_events"), 3.0),
    (("scheme_c", "contributions"), 2.0),
    (("pool_summary", "buy_total"), True),
])
def test_brief_numeric_types_must_match_strictly(path, value):
    """True/3.0 不得冒充 1/3（探针侧已是严格 int 口径）。"""
    items = list(evidence())
    target = items[2]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    assert reconcile_evidence(DAY, *items)["stages"]["brief"]["status"] == "REJECTED"


@pytest.mark.parametrize("superseded", [
    [{}] * 9,
    [{"action": "buy", "score": "87.0", "dt": None, "rationale": ""}],
    [{"action": "buy", "score": 87.0, "dt": None}],
    [{"action": "buy", "score": 87.0, "dt": None, "rationale": "", "extra": 1}],
    [{"action": "", "score": 87.0, "dt": None, "rationale": ""}],
    [{"action": "buy", "score": 87, "dt": None, "rationale": ""}],
    [{"action": "buy", "score": 87.0, "dt": None, "rationale": "x" * 81}],
    ["x"],
])
def test_fabricated_superseded_entries_cannot_inflate_counts(superseded):
    """10 条源事件不能只由 1 行真实内容 + 空对象充数支撑。"""
    probe, _, _, _ = evidence()
    probe["counts"] = {"source_events": 10, "signals": 10, "kept": 1,
                       "combined": 1, "pool": 1}
    probe["combined"] = [{"trade_date": DAY, "symbol": "002634.SZ", "action": "buy",
                          "contributions": {"chan_monitor": {"superseded": superseded}}}]
    probe["pool"] = {"scope": "RESEARCH_ONLY_NOT_PRODUCTION",
                     "cutover_verdict": "BLOCK_CUTOVER", "trade_date": DAY,
                     "stats": {"combined_total": 1, "buy_total": 1,
                               "superseded_total": len(superseded)},
                     "pool": [{"symbol": "002634.SZ", "action": "buy"}]}
    result = reconcile_evidence(DAY, probe)
    assert result["stages"]["combined"]["status"] == "REJECTED"
    assert_blocked(result)


def test_real_historical_producer_feeds_reconciliation(tmp_path):
    """真实 inspect_historical_pool 产出（含 matched_events）须可被对账消费。"""
    rows = [
        {"market": "CN", "code": "002633.SZ", "type": "2买", "dt": "2026-10-18",
         "detect_date": "2026-10-19", "price": 10.0, "mbei": None, "bei": None,
         "bei2": None, "vv": True},
        {"market": "CN", "code": "002634.SZ", "type": "2买", "dt": "2026-10-18",
         "detect_date": "2026-10-19", "price": 11.0, "mbei": None, "bei": None,
         "bei2": None, "vv": True},
        {"market": "CN", "code": "002635.SZ", "type": "2买", "dt": "2026-10-10",
         "detect_date": "2026-10-15", "price": 12.0, "mbei": None, "bei": None,
         "bei2": None, "vv": True},
    ]
    source = tmp_path / "pool.jsonl"
    source.write_bytes("".join(json.dumps(row, ensure_ascii=False) + "\n"
                               for row in rows).encode("utf-8"))
    probe = inspect_historical_pool(DAY, source)
    assert probe["chain_status"] == "VERIFIED_IN_MEMORY"
    assert probe["counts"]["source_events"] == 3
    assert probe["counts"]["matched_events"] == 2
    result = reconcile_evidence(DAY, probe, brief=probe["brief"])
    assert result["stages"]["source"]["count"] == 3
    assert result["counts"]["matched_events"] == result["counts"]["signals"] == 2
    assert result["stages"]["brief"]["status"] == "IN_MEMORY_ONLY"
    assert_blocked(result)
    # 同一签核参数：历史多日池整池 3 条不能冒充当日 2 条的单日签名
    attestation = dict(evidence()[1], event_count=3)
    assert reconcile_evidence(DAY, probe, attestation)["stages"]["source"]["status"] == "REJECTED"


def test_source_attestation_rejected_branch_has_own_reason():
    """P2-5 专用分支须自带可区分理由，避免与"状态不可信"兜底行为等价。"""
    probe, att, brief, receipt = evidence()
    att["status"] = "REJECTED"
    result = reconcile_evidence(DAY, probe, att, brief, receipt)
    assert result["stages"]["source"]["status"] == "REJECTED"
    assert any("明确失败" in reason for reason in result["reasons"])


@pytest.mark.parametrize("mutate", [
    lambda p: p["combined"][0].update(
        {"contributions": {"": {"superseded": [_superseded()]}}}),
    lambda p: p["combined"][0].update({"contributions": {"chan_monitor": ["x"]}}),
    lambda p: p["combined"][0]["contributions"]["chan_monitor"].update(
        {"superseded": [{"action": "buy", "score": 87.0, "dt": 5, "rationale": ""}]}),
    lambda p: p["combined"][0]["contributions"]["chan_monitor"].update(
        {"superseded": [{"action": "buy", "score": 87.0, "dt": ["2026-10-18"],
                         "rationale": ""}]}),
])
def test_malformed_contribution_shapes_are_rejected(mutate):
    probe, att, brief, receipt = evidence()
    mutate(probe)
    result = reconcile_evidence(DAY, probe, att, brief, receipt)
    assert result["stages"]["combined"]["status"] == "REJECTED"
    assert_blocked(result)


@pytest.mark.parametrize("key,value", [
    ("buy_total", True),
    ("superseded_total", 1.0),
    ("combined_total", 2.0),
    ("combined_total", 3),
])
def test_pool_stats_must_match_strictly(key, value):
    items = list(evidence())
    items[0]["pool"]["stats"][key] = value
    result = reconcile_evidence(DAY, *items)
    assert result["stages"]["pool"]["status"] == "REJECTED"
    assert_blocked(result)


def test_brief_cannot_self_upgrade_source_verdict():
    items = list(evidence())
    items[2]["real_source_verdict"] = "READY"
    assert reconcile_evidence(DAY, *items)["stages"]["brief"]["status"] == "REJECTED"


def test_real_superseded_chain_feeds_reconciliation(tmp_path):
    """真实同票多信号（kept<signals, superseded>0）须被四键校验接受而非误拒。"""
    rows = [
        {"market": "CN", "code": "002633.SZ", "type": "1买", "dt": "2026-10-16",
         "detect_date": "2026-10-19", "price": 10.0, "mbei": None, "bei": None,
         "bei2": None, "vv": True},
        {"market": "CN", "code": "002633.SZ", "type": "1卖", "dt": "2026-10-18",
         "detect_date": "2026-10-19", "price": 10.0, "mbei": None, "bei": None,
         "bei2": None, "vv": True},
    ]
    source = tmp_path / "source.jsonl"
    raw = "".join(json.dumps(row, ensure_ascii=False) + "\n"
                  for row in rows).encode("utf-8")
    source.write_bytes(raw)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "business_date": DAY, "observed_date": "2026-10-20",
        "source_path": str(source.resolve()),
        "source_sha256": hashlib.sha256(raw).hexdigest(), "event_count": 2,
    }), encoding="utf-8")
    probe = inspect_batch(DAY, source, manifest)
    assert probe["chain_status"] == "VERIFIED_IN_MEMORY"
    assert probe["counts"]["signals"] == 2 and probe["counts"]["kept"] == 1
    assert len(probe["combined"][0]["contributions"]["chan_monitor"]["superseded"]) == 1
    result = reconcile_evidence(DAY, probe, brief=build_research_brief(probe))
    assert result["stages"]["combined"]["status"] == "IN_MEMORY_ONLY"
    assert result["counts"]["pool"] == 0
    assert_blocked(result)


@pytest.mark.parametrize("bad", ["20260230", "2026-10-19", None])
def test_invalid_requested_day(bad):
    with pytest.raises(ValueError):
        reconcile_evidence(bad)
