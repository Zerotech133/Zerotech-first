"""WO-QT-20261020-046：R0 全流程合成演练的离线对抗测试。"""
import base64
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest
from cryptography.hazmat.primitives import serialization

from strategy_layer.r0_historical_pool_probe import inspect_historical_pool
from strategy_layer.r0_m3_drill import (
    DEFAULT_BUSINESS_DAY as DAY,
    DRILL_SCOPE,
    RECEIVER_IDENTITY,
    SOURCE_ISSUER_ID,
    canonical_json_bytes,
    issuer_private_key,
    issue_source_attestation,
    raw_public_key,
    receiver_private_key,
    run_full_drill,
    sign_drill_receipt,
    synthetic_drill_rows,
    write_drill_source,
)
from strategy_layer.r0_receipt_attestation import verify_receipt_attestation
from strategy_layer.r0_receipt_replay_guard import ReplayLedger
from strategy_layer.r0_research_brief import build_research_brief
from strategy_layer.r0_source_attestation import verify_source_attestation

NOW = datetime(2026, 10, 20, 16, 0, 0, tzinfo=timezone.utc)
STAGE_ORDER = ("source", "signal", "combined", "pool", "brief", "receipt")


def _has_ready_value(value) -> bool:
    if isinstance(value, str):
        return value == "READY"
    if isinstance(value, dict):
        return any(_has_ready_value(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_ready_value(item) for item in value)
    return False


def _brief_and_bytes(tmp_path):
    """走真实生产者：探针 → 简报 → 模拟通道规范化字节。"""
    source_path, raw = write_drill_source(
        synthetic_drill_rows(DAY), tmp_path, business_day=DAY)
    probe = inspect_historical_pool(DAY, source_path)
    assert probe["chain_status"] == "VERIFIED_IN_MEMORY"
    brief = build_research_brief(probe)
    return brief, canonical_json_bytes(brief)


def test_full_drill_passes_all_segments_and_stays_blocked(tmp_path):
    rows = synthetic_drill_rows(DAY)
    original = deepcopy(rows)
    report = run_full_drill(rows, tmp_path, now=NOW)
    assert rows == original  # 输入不被修改
    # 字面量断言防"常量被改仍绿"：水印值本身是演练身份的一部分
    assert report["scope"] == "SYNTHETIC_DRILL_NOT_M3_EVIDENCE"
    assert report["status"] == "BLOCKED_NOT_READY"
    assert report["stopped_at"] is None
    seg = report["segments"]
    assert seg["source_attestation"]["status"] == "VERIFIED"
    assert seg["source_attestation"]["issuer_id"] == SOURCE_ISSUER_ID
    assert seg["source_attestation"]["event_count"] == 2
    assert seg["probe"]["status"] == "UNVERIFIED"
    assert seg["probe"]["chain_status"] == "VERIFIED_IN_MEMORY"
    assert seg["probe"]["counts"] == {"source_events": 2, "matched_events": 2,
                                      "signals": 2, "kept": 2, "combined": 2,
                                      "pool": 2}
    assert seg["brief"]["status"] == "BUILT_IN_MEMORY"
    assert seg["receipt"]["status"] == "VERIFIED_RECEIPT_ONLY"
    assert seg["replay_guard"]["status"] == "ENFORCED"
    assert seg["replay_guard"]["claims"] == ["CLAIMED", "DUPLICATE"]
    assert seg["reconciliation"]["status"] == "NOT_VERIFIED"
    assert [seg["reconciliation"]["stages"][s]["status"]
            for s in STAGE_ORDER] == [
        "CALLER_ATTESTATION_CLAIM", "IN_MEMORY_ONLY", "IN_MEMORY_ONLY",
        "IN_MEMORY_ONLY", "IN_MEMORY_ONLY", "CALLER_RECEIPT_CLAIM"]
    assert seg["reconciliation"]["counts"]["brief"] == 1
    assert seg["reconciliation"]["counts"]["receipt"] == 1
    final = report["final"]
    assert final["real_source_verdict"] == "BLOCKED_REAL_DATA"
    assert final["cutover_verdict"] == "BLOCK_CUTOVER"
    assert final["status"] != "READY"
    assert not _has_ready_value(report)


def test_drill_is_repeatable_under_fixed_keys_and_clock(tmp_path):
    rows = synthetic_drill_rows(DAY)
    first = run_full_drill(rows, tmp_path / "run1", now=NOW)
    second = run_full_drill(rows, tmp_path / "run2", now=NOW)
    assert first == second


def test_rerun_in_same_workdir_fails_closed_on_replay(tmp_path):
    rows = synthetic_drill_rows(DAY)
    assert run_full_drill(rows, tmp_path, now=NOW)["status"] == "BLOCKED_NOT_READY"
    replay = run_full_drill(rows, tmp_path, now=NOW)
    assert replay["status"] == "FAILED"
    assert replay["stopped_at"] == "replay_guard"
    assert replay["scope"] == DRILL_SCOPE


def test_tampered_brief_byte_fails_receipt_even_after_resign(tmp_path):
    brief, brief_bytes = _brief_and_bytes(tmp_path)
    marker = b"RESEARCH_ONLY_NOT_PRODUCTION"
    index = brief_bytes.index(marker)
    tampered = (brief_bytes[:index] + b"RESEARCH_ONLY_NOT_PRODUCTXON"
                + brief_bytes[index + len(marker):])
    assert tampered != brief_bytes and len(tampered) == len(brief_bytes)
    # (a) 即使对篡改字节重新回签，简报研究边界绑定仍拒绝
    resigned = sign_drill_receipt(
        receiver_private_key(), tampered, batch_id=DAY,
        source_identity=brief["source"]["sha256"],
        received_at=NOW.isoformat(), message_id="drill-tamper-a")
    assert verify_receipt_attestation(
        tampered, resigned,
        trusted_receiver_public_key=raw_public_key(receiver_private_key()),
        expected_batch_id=DAY,
        expected_source_identity=brief["source"]["sha256"],
        expected_receiver_identity=RECEIVER_IDENTITY,
        seen_message_ids=set(), now=NOW) == {"status": "NOT_VERIFIED"}
    # (b) 篡改非绑定字段但沿用原回执：brief_sha256 绑定拒绝
    benign = brief_bytes.replace(b"manifest", b"manifesu", 1)
    receipt = sign_drill_receipt(
        receiver_private_key(), brief_bytes, batch_id=DAY,
        source_identity=brief["source"]["sha256"],
        received_at=NOW.isoformat(), message_id="drill-tamper-b")
    assert verify_receipt_attestation(
        benign, receipt,
        trusted_receiver_public_key=raw_public_key(receiver_private_key()),
        expected_batch_id=DAY,
        expected_source_identity=brief["source"]["sha256"],
        expected_receiver_identity=RECEIVER_IDENTITY,
        seen_message_ids=set(), now=NOW) == {"status": "NOT_VERIFIED"}


def test_duplicate_message_id_claim_is_duplicate(tmp_path):
    ledger = ReplayLedger(str(tmp_path / "ledger.json"))
    assert ledger.claim("drill-msg-1")["status"] == "CLAIMED"
    assert ledger.claim("drill-msg-1")["status"] == "DUPLICATE"
    assert ledger.claim("drill-msg-2")["status"] == "CLAIMED"


def test_mixed_detect_date_fails_drill_at_source(tmp_path):
    rows = synthetic_drill_rows(DAY)
    rows.append({"market": "CN", "code": "002635.SZ", "type": "2买",
                 "dt": "2026-10-19", "detect_date": "2026-10-19",
                 "price": 12.0, "mbei": None, "bei": None, "bei2": None,
                 "vv": True})
    report = run_full_drill(rows, tmp_path, now=NOW)
    assert report["status"] == "FAILED"
    assert report["stopped_at"] == "source_attestation"
    assert report["segments"]["source_attestation"]["status"] == "REJECTED"
    assert report["segments"]["probe"]["status"] == "NOT_RUN"
    assert report["scope"] == DRILL_SCOPE
    assert report["final"]["real_source_verdict"] == "BLOCKED_REAL_DATA"
    assert report["final"]["cutover_verdict"] == "BLOCK_CUTOVER"


def test_missing_trusted_public_keys_stays_unverified(tmp_path):
    rows = synthetic_drill_rows(DAY)
    _, raw = write_drill_source(rows, tmp_path, business_day=DAY)
    attestation = issue_source_attestation(issuer_private_key(), DAY, raw)
    for roots in (None, {}):
        result = verify_source_attestation(
            DAY, raw, attestation, trusted_public_keys=roots,
            trusted_as_of=NOW)
        assert result["status"] == "UNVERIFIED"
        assert result["identity_status"] == "UNVERIFIED"
        assert result["historical_single_day_frozen"] is False
        assert result["real_source_verdict"] == "BLOCKED_REAL_DATA"


@pytest.mark.parametrize("field,value", [
    ("event_count", 99), ("business_date", "20261019"),
    ("source_sha256", "0" * 64), ("issued_at", "2026-10-19T08:00:00Z"),
])
def test_resigned_tampered_source_claims_rejected(tmp_path, field, value):
    rows = synthetic_drill_rows(DAY)
    _, raw = write_drill_source(rows, tmp_path, business_day=DAY)
    attestation = issue_source_attestation(issuer_private_key(), DAY, raw)
    attestation["claims"][field] = value
    payload = json.dumps(attestation["claims"], sort_keys=True,
                         separators=(",", ":"), ensure_ascii=False,
                         allow_nan=False).encode("utf-8")
    attestation["signature"] = base64.b64encode(
        issuer_private_key().sign(payload)).decode("ascii")
    result = verify_source_attestation(
        DAY, raw, attestation,
        trusted_public_keys={SOURCE_ISSUER_ID: raw_public_key(issuer_private_key())},
        trusted_as_of=NOW)
    assert result["status"] == "REJECTED"
    assert result["real_source_verdict"] == "BLOCKED_REAL_DATA"


def test_receipt_received_at_before_business_day_rejected(tmp_path):
    brief, brief_bytes = _brief_and_bytes(tmp_path)
    early = datetime(2026, 10, 19, 23, 59, 59, tzinfo=timezone.utc)
    receipt = sign_drill_receipt(
        receiver_private_key(), brief_bytes, batch_id=DAY,
        source_identity=brief["source"]["sha256"],
        received_at=early.isoformat(), message_id="drill-early-1")
    assert verify_receipt_attestation(
        brief_bytes, receipt,
        trusted_receiver_public_key=raw_public_key(receiver_private_key()),
        expected_batch_id=DAY,
        expected_source_identity=brief["source"]["sha256"],
        expected_receiver_identity=RECEIVER_IDENTITY,
        seen_message_ids=set(), now=NOW) == {"status": "NOT_VERIFIED"}


def test_drill_helpers_never_mutate_input_rows(tmp_path):
    rows = synthetic_drill_rows(DAY)
    original = deepcopy(rows)
    _, raw = write_drill_source(rows, tmp_path, business_day=DAY)
    issue_source_attestation(issuer_private_key(), DAY, raw)
    assert rows == original



# ---- WO-QT-20261020-054：046 评审 P2 防御性加固的反例 ----

def test_final_guard_failure_resets_gate_verdicts_to_literals(monkeypatch, tmp_path):
    """FINAL_GUARD_FAILED 路径：失败报告门位必须是字面量 BLOCKED，不得残留对账中间值。"""
    from strategy_layer import r0_m3_drill as drill_mod
    expected_stages = ("CALLER_ATTESTATION_CLAIM", "IN_MEMORY_ONLY",
                       "IN_MEMORY_ONLY", "IN_MEMORY_ONLY",
                       "IN_MEMORY_ONLY", "CALLER_RECEIPT_CLAIM")
    tampered = {
        "status": "NOT_VERIFIED",
        "real_source_verdict": "TAMPERED_NOT_BLOCKED",
        "cutover_verdict": "BLOCK_CUTOVER",
        "stages": {name: {"status": status}
                   for name, status in zip(STAGE_ORDER, expected_stages)},
        "reasons": [], "counts": {},
    }
    monkeypatch.setattr(drill_mod, "reconcile_evidence",
                        lambda *a, **k: tampered)
    report = drill_mod.run_full_drill(
        synthetic_drill_rows(DAY), tmp_path, now=NOW)
    assert report["status"] == "FAILED"
    assert report["stopped_at"] == "reconciliation"
    assert report["segments"]["reconciliation"]["status"] == "FINAL_GUARD_FAILED"
    assert report["final"]["real_source_verdict"] == "BLOCKED_REAL_DATA"
    assert report["final"]["cutover_verdict"] == "BLOCK_CUTOVER"


def test_unexpected_exception_keeps_gate_verdicts_blocked(monkeypatch, tmp_path):
    """异常路径：结构化失败报告的双门位恒为字面量 BLOCKED（含 _blank_report 默认）。"""
    from strategy_layer import r0_m3_drill as drill_mod

    def _boom(*args, **kwargs):
        raise RuntimeError("synthetic probe fault")

    monkeypatch.setattr(drill_mod, "inspect_historical_pool", _boom)
    report = drill_mod.run_full_drill(
        synthetic_drill_rows(DAY), tmp_path, now=NOW)
    assert report["status"] == "FAILED"
    assert report["stopped_at"] == "probe"
    assert report["segments"]["probe"]["status"] == "UNEXPECTED_EXCEPTION"
    assert "RuntimeError" in report["segments"]["probe"]["reason"]
    assert report["final"]["real_source_verdict"] == "BLOCKED_REAL_DATA"
    assert report["final"]["cutover_verdict"] == "BLOCK_CUTOVER"



# ---- WO-QT-20261020-054：046 评审 P2 防御性加固的反例 ----

def test_final_guard_failure_resets_gate_verdicts_to_literals(monkeypatch, tmp_path):
    """FINAL_GUARD_FAILED 路径：失败报告门位必须是字面量 BLOCKED，不得残留对账中间值。"""
    from strategy_layer import r0_m3_drill as drill_mod
    expected_stages = ("CALLER_ATTESTATION_CLAIM", "IN_MEMORY_ONLY",
                       "IN_MEMORY_ONLY", "IN_MEMORY_ONLY",
                       "IN_MEMORY_ONLY", "CALLER_RECEIPT_CLAIM")
    tampered = {
        "status": "NOT_VERIFIED",
        "real_source_verdict": "TAMPERED_NOT_BLOCKED",
        "cutover_verdict": "BLOCK_CUTOVER",
        "stages": {name: {"status": status}
                   for name, status in zip(STAGE_ORDER, expected_stages)},
        "reasons": [], "counts": {},
    }
    monkeypatch.setattr(drill_mod, "reconcile_evidence",
                        lambda *a, **k: tampered)
    report = drill_mod.run_full_drill(
        synthetic_drill_rows(DAY), tmp_path, now=NOW)
    assert report["status"] == "FAILED"
    assert report["stopped_at"] == "reconciliation"
    assert report["segments"]["reconciliation"]["status"] == "FINAL_GUARD_FAILED"
    assert report["final"]["real_source_verdict"] == "BLOCKED_REAL_DATA"
    assert report["final"]["cutover_verdict"] == "BLOCK_CUTOVER"


def test_unexpected_exception_keeps_gate_verdicts_blocked(monkeypatch, tmp_path):
    """异常路径：结构化失败报告的双门位恒为字面量 BLOCKED（含 _blank_report 默认）。"""
    from strategy_layer import r0_m3_drill as drill_mod

    def _boom(*args, **kwargs):
        raise RuntimeError("synthetic probe fault")

    monkeypatch.setattr(drill_mod, "inspect_historical_pool", _boom)
    report = drill_mod.run_full_drill(
        synthetic_drill_rows(DAY), tmp_path, now=NOW)
    assert report["status"] == "FAILED"
    assert report["stopped_at"] == "probe"
    assert report["segments"]["probe"]["status"] == "UNEXPECTED_EXCEPTION"
    assert "RuntimeError" in report["segments"]["probe"]["reason"]
    assert report["final"]["real_source_verdict"] == "BLOCKED_REAL_DATA"
    assert report["final"]["cutover_verdict"] == "BLOCK_CUTOVER"
