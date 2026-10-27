# -*- coding: utf-8 -*-
"""WO-QT-20261020-056：R1 真实数据试运行器的离线对抗测试。

源用合成行（链路函数已有真实数据守护；本文件只验证运行器编排、
密钥分离、终报门位与披露完整性）。全程 tmp 目录，不触真实数据文件。
凭证-源归属契约：签发业务日必须与全部源行 detect_date 严格一致。
"""

import json
from datetime import datetime, timezone

import pytest

from strategy_layer.r0_m3_drill import synthetic_drill_rows, write_drill_source
from strategy_layer.r0_m3_real_trial import (
    TRIAL_ISSUER_ID,
    TRIAL_RECEIVER_IDENTITY,
    run_real_trial,
    save_report,
)

NOW = datetime(2026, 10, 20, 16, 0, 0, tzinfo=timezone.utc)


def _has_ready_value(value):
    """字面量值级检查（非子串）：BLOCKED_NOT_READY 不算 READY。"""
    if isinstance(value, str):
        return value == "READY"
    if isinstance(value, dict):
        return any(_has_ready_value(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_ready_value(v) for v in value)
    return False


def _make_source(tmp_path, day, name):
    _, raw = write_drill_source(
        synthetic_drill_rows(day), tmp_path / name, business_day=day
    )
    path = tmp_path / name / "pool.jsonl"
    path.write_bytes(raw)
    return path


@pytest.fixture()
def source_file(tmp_path):
    return _make_source(tmp_path, "20261020", "src")


def test_full_trial_chain_blocked_and_disclosed(tmp_path, source_file):
    work = tmp_path / "trial"
    pub = tmp_path / "pubkeys"
    report = run_real_trial(source_file, work, "20261020", pubkey_out_dir=pub, now=NOW)
    # 终报门位：恒研究口径 + BLOCKED 双门位 + 值级无 READY（字面量断言）
    assert report["scope"] == "RESEARCH_ONLY_NOT_PRODUCTION"
    assert report["real_source_verdict"] == "BLOCKED_REAL_DATA"
    assert report["cutover_verdict"] == "BLOCK_CUTOVER"
    assert _has_ready_value(report) is False
    # 六段全走通
    assert [
        report["segments"][k]["status"]
        for k in (
            "freeze",
            "source_attestation",
            "probe",
            "receipt",
            "replay_guard",
            "reconciliation",
        )
    ] == [
        "FROZEN",
        "VERIFIED",
        "UNVERIFIED",
        "VERIFIED_RECEIPT_ONLY",
        "ENFORCED",
        "NOT_VERIFIED",
    ]
    assert report["status"] == "BLOCKED_NOT_READY"
    assert report["trial_outcome"]["matched_events"] == 2
    # 试运行披露逐字在报
    assert any("代为执行" in d for d in report["disclosures"])
    assert any("trial-grade" in d for d in report["disclosures"])
    # 一次性私钥生命周期终结（WO-061）：文件用后销毁 + 终报留痕
    assert not (work / "keys" / "issuer_private.pem").exists()
    assert not (work / "keys" / "receiver_private.pem").exists()
    assert report["segments"]["key_hygiene"]["status"] == "DESTROYED"
    assert set(report["segments"]["key_hygiene"]["files"]) == {
        "issuer_private.pem",
        "receiver_private.pem",
    }
    # 公钥仍落独立 pub 目录；私钥绝不外流
    assert (pub / "issuer_public.pem").is_file()
    assert (pub / "receiver_public.pem").is_file()
    assert not (pub / "issuer_private.pem").exists()
    # 冻结证据落地：manifest 记录源文件 mtime（披露承诺的可核证据）
    manifest = json.loads(
        next(
            p
            for p in work.glob("*.json")
            if json.loads(p.read_text(encoding="utf-8")).get("freeze_basis")
        ).read_text(encoding="utf-8")
    )
    assert isinstance(manifest.get("source_mtime_utc"), float)
    assert report["issuer_id"] == TRIAL_ISSUER_ID
    assert report["receiver_identity"] == TRIAL_RECEIVER_IDENTITY
    # 终报可落盘且字段一致
    path = save_report(report, work)
    reloaded = json.loads(path.read_text(encoding="utf-8"))
    assert reloaded["status"] == report["status"]


def test_empty_batch_no_match_is_legal_terminal(tmp_path):
    """空批（业务日零检出事件）是合法终态：全链照走，BLOCKED_NOT_READY，
    不伪造事件、零匹配不等于可信零事件（探针自动标注进简报风险）。"""
    src = tmp_path / "empty" / "pool.jsonl"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"")
    report = run_real_trial(
        src, tmp_path / "t2", "20261020", pubkey_out_dir=tmp_path / "pub2", now=NOW
    )
    assert report["segments"]["probe"]["status"] == "NO_MATCH_UNVERIFIED"
    assert report["status"] == "BLOCKED_NOT_READY"
    # WO-061：空批在对账层同样是合法终态——豁免后 NOT_VERIFIED，
    # 不得被 reconcile REJECTED 后又被顶层洗白（终报门检钉住）
    assert report["segments"]["reconciliation"]["status"] == "NOT_VERIFIED"
    assert report["final"]["status"] == "NOT_VERIFIED"
    assert report["trial_outcome"]["matched_events"] is None  # 未匹配：不适用
    assert "零匹配" in report["trial_outcome"]["note"]
    assert _has_ready_value(report) is False


def test_cross_day_attestation_rejected(tmp_path, source_file):
    """凭证-源归属契约：10-20 的源行签发为 10-19 凭证 → 验签显式拒绝。"""
    report = run_real_trial(
        source_file,
        tmp_path / "t3",
        "20261019",
        pubkey_out_dir=tmp_path / "pub3",
        now=NOW,
    )
    assert report["status"] == "FAILED"
    assert report["stopped_at"] == "source_attestation"
    assert report["segments"]["source_attestation"]["status"] == "REJECTED"
    assert report["real_source_verdict"] == "BLOCKED_REAL_DATA"


def test_replay_message_id_differs_per_day(tmp_path):
    """不同业务日（各有各的源批）message_id 不同。"""
    src20 = _make_source(tmp_path, "20261020", "d20")
    src19 = _make_source(tmp_path, "20261019", "d19")
    r1 = run_real_trial(
        src20, tmp_path / "w1", "20261020", pubkey_out_dir=tmp_path / "p1", now=NOW
    )
    r2 = run_real_trial(
        src19, tmp_path / "w2", "20261019", pubkey_out_dir=tmp_path / "p2", now=NOW
    )
    assert r1["status"] == "BLOCKED_NOT_READY"
    assert r2["status"] == "BLOCKED_NOT_READY"
    assert (
        r1["segments"]["receipt"]["message_id"]
        != r2["segments"]["receipt"]["message_id"]
    )
