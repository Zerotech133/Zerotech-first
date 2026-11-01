# -*- coding: utf-8 -*-
"""D1 校准测试：①扩席门（tools/d1_calibration.py）；②置信度分桶校准
（agent_platform/d1_calibration.py，WO-D1-CALIB-20261101，只读）。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import d1_calibration as C  # noqa: E402
from agent_platform import d1_calibration as CAL  # noqa: E402


def _row(rec, conf, hit):
    return {"recommendation": rec, "confidence": conf, "hit": hit}


def test_gate_blocked_small_sample():
    rows = [_row("BUY", 0.7, 1), _row("BUY", 0.8, 0)]
    rep = C.evaluate(rows)
    assert rep["can_expand"] is False
    assert rep["n_directional"] == 2
    assert rep["brier"] is not None


def test_gate_blocked_no_metrics():
    rep = C.evaluate([])
    assert rep["can_expand"] is False
    assert "Brier 不可计算" in rep["reasons"]


def test_gate_pass_when_enough_and_metrics():
    rows = [_row("BUY", 0.8, 1) for _ in range(20)] + [_row("SELL", 0.2, 0) for _ in range(20)]
    rep = C.evaluate(rows)
    assert rep["can_expand"] is True
    assert rep["n_directional"] == 40
    assert 0 <= rep["ece"] <= 1


def test_non_directional_ignored():
    rows = [_row("WATCH", 0.5, None), _row("HOLD", 0.5, None)] + [
        _row("BUY", 0.8, 1) for _ in range(30)]
    rep = C.evaluate(rows)
    assert rep["n_directional"] == 30
    assert rep["can_expand"] is True


# ============ P1 置信度分桶校准（WO-D1-CALIB-20261101 · 只读）============
# 上方 4 个用例守的是「扩席门」（tools/d1_calibration.py）；以下守的是
# agent_platform/d1_calibration.py 的分桶/Brier/未测量语义。两套互不替代。


def _crow(conf, outcome=None, decision_id="d1-20261101000000-000001"):
    """决策行夹具：只带校准需要的字段，不碰真实账本。"""
    row = {"decision_id": decision_id, "code": "002445.SZ", "tier": "BUY",
           "confidence": conf}
    if outcome is not None:
        row["outcome"] = outcome
    return row


def _ledger(tmp_path, rows, name="d1_decisions.jsonl"):
    path = tmp_path / name
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8")
    return path


def test_empty_ledger_is_unmeasured(tmp_path):
    """空账/账本不存在：measured=0 → status=unmeasured，不假装校准成功。"""
    ledger = tmp_path / "missing.jsonl"
    rep = CAL.calibration_report(ledger=ledger)
    assert rep["total"] == 0
    assert rep["measured"] == 0
    assert rep["unmeasured"] == 0
    assert rep["invalid"] == 0
    assert rep["overall_brier"] is None
    assert rep["status"] == CAL.STATUS_UNMEASURED == "unmeasured"
    assert rep["readonly"] is True
    assert [b["bucket"] for b in rep["reliability"]] == list(CAL.BUCKET_LABELS)
    assert all(b["count"] == 0 and b["mean_confidence"] is None
               and b["win_rate"] is None and b["brier"] is None
               for b in rep["reliability"])
    assert not ledger.exists()  # 只读：连空账都不新建
    assert CAL.brier_score([]) is None


def test_brier_exact_value():
    """Brier = 均方误差；手算值必须一字不差。"""
    assert CAL.brier_score([_crow(0.8, 1, "d1-a"), _crow(0.6, 0, "d1-b")]) == 0.2
    assert CAL.brier_score([_crow(0.5, 1, "d1-a"), _crow(0.5, 0, "d1-b")]) == 0.25
    assert CAL.brier_score([_crow(0.9, 1, "d1-a"), _crow(0.1, 0, "d1-b")]) == 0.01
    assert CAL.brier_score([_crow(1.0, 1)]) == 0.0


def test_bucket_boundaries():
    """五桶左闭右开、最后一桶含 1.0；分桶不重不漏。"""
    assert CAL.bucket_of(0.0) == "0-0.2"
    assert CAL.bucket_of(0.1999) == "0-0.2"
    assert CAL.bucket_of(0.2) == "0.2-0.4"
    assert CAL.bucket_of(0.3999999) == "0.2-0.4"
    assert CAL.bucket_of(0.4) == "0.4-0.6"
    assert CAL.bucket_of(0.6) == "0.6-0.8"
    assert CAL.bucket_of(0.7999) == "0.6-0.8"
    assert CAL.bucket_of(0.8) == "0.8-1.0"
    assert CAL.bucket_of(1.0) == "0.8-1.0"
    assert CAL.BUCKET_LABELS == ("0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1.0")
    assert {CAL.bucket_of(i / 100.0) for i in range(100)} == set(CAL.BUCKET_LABELS)


def test_missing_outcome_counted_not_filled(tmp_path):
    """缺 outcome 只记 unmeasured，不按 0/1 硬填、不进桶。"""
    rows = [
        _crow(0.7, 1, "d1-a"),
        {"decision_id": "d1-b", "confidence": 0.6},
        {"decision_id": "d1-c", "confidence": 0.9, "outcome": None},
    ]
    rep = CAL.calibration_report(ledger=_ledger(tmp_path, rows))
    assert rep["total"] == 3
    assert rep["measured"] == 1
    assert rep["unmeasured"] == 2
    assert rep["invalid"] == 0
    assert rep["overall_brier"] == 0.09
    assert sum(b["count"] for b in rep["reliability"]) == 1
    assert rep["status"] == CAL.STATUS_INSUFFICIENT


def test_bad_rows_are_invalid_or_skipped(tmp_path):
    """坏行不抛异常：非 dict/缺 confidence/非数字计 invalid；坏 JSON 行不进 total。"""
    rows = [None, 42, "nope", {}, {"confidence": None, "outcome": 1},
            {"confidence": "high", "outcome": 1}, {"outcome": 1}]
    assert CAL.brier_score(rows) is None
    assert sum(b["count"] for b in CAL.reliability(rows)) == 0

    ledger = tmp_path / "d1_decisions.jsonl"
    ledger.write_text(
        '{"confidence": 0.5, "outcome": 1}\n'
        'not-json\n'
        '[1, 2]\n'
        '\n'
        '{"confidence": 0.8}\n',
        encoding="utf-8")
    rep = CAL.calibration_report(ledger=ledger)
    assert rep["total"] == 2  # 坏行/数组行被 read_decisions 跳过
    assert rep["measured"] == 1
    assert rep["unmeasured"] == 1
    assert rep["invalid"] == 0


def test_confidence_out_of_range_is_invalid(tmp_path):
    """置信度越界/非数字：bucket_of → None，报告计 invalid，不裁剪进桶。"""
    for bad in (1.2, -0.1, 2, "0.5x", None, True, False,
                float("inf"), float("nan")):
        assert CAL.bucket_of(bad) is None
    rows = [_crow(1.2, 1, "d1-a"), _crow(-0.1, 0, "d1-b"), _crow(0.5, 0, "d1-c")]
    assert CAL.brier_score(rows) == 0.25  # 只有 0.5 那条可测
    rep = CAL.calibration_report(ledger=_ledger(tmp_path, rows))
    assert rep["total"] == 3
    assert rep["measured"] == 1
    assert rep["invalid"] == 2
    assert rep["overall_brier"] == 0.25


def test_reliability_bucket_stats():
    """每桶 count / 平均置信度 / 命中率 / Brier 逐项对账。"""
    rows = [
        {"confidence": 0.1, "outcome": 0},
        {"confidence": 0.15, "outcome": 1},
        {"confidence": 0.85, "outcome": 1},
    ]
    buckets = {b["bucket"]: b for b in CAL.reliability(rows)}
    low = buckets["0-0.2"]
    assert low["count"] == 2
    assert low["mean_confidence"] == 0.125
    assert low["win_rate"] == 0.5
    assert low["brier"] == 0.36625
    high = buckets["0.8-1.0"]
    assert high["count"] == 1
    assert high["mean_confidence"] == 0.85
    assert high["win_rate"] == 1.0
    assert high["brier"] == 0.0225
    empty = buckets["0.4-0.6"]
    assert empty["count"] == 0 and empty["brier"] is None
    assert [b["count"] for b in CAL.reliability(rows)] == [2, 0, 0, 0, 1]


def test_outcomes_param_mapping_and_rows(tmp_path):
    """outcomes 既支持 {decision_id: 0/1} 映射，也支持 sidecar 行列表（hit）。"""
    rows = [_crow(0.8, None, "d1-a"), _crow(0.6, None, "d1-b"),
            _crow(0.9, None, "d1-c")]
    mapping = {"d1-a": 1, "d1-b": 0}  # d1-c 缺 → unmeasured
    assert CAL.brier_score(rows, mapping) == 0.2
    sidecar = [{"decision_id": "d1-a", "hit": 1}, {"decision_id": "d1-b", "hit": 0}]
    assert CAL.brier_score(rows, sidecar) == 0.2
    rep = CAL.calibration_report(ledger=_ledger(tmp_path, rows), outcomes=mapping)
    assert rep["measured"] == 2
    assert rep["unmeasured"] == 1
    assert rep["overall_brier"] == 0.2
    # 映射里没有的行：退回收行内 outcome，仍查不到才算未测量（不猜）
    assert CAL.brier_score([_crow(0.8, 1, "d1-z")], {"d1-other": 0}) == 0.04


def test_deterministic_and_readonly(tmp_path):
    """同输入两次报告逐字节一致；账本文件一字节不变。"""
    rows = [_crow(0.2, 1, "d1-a"), _crow(0.55, 0, "d1-b"), _crow(0.75, 1, "d1-c")]
    ledger = _ledger(tmp_path, rows)
    before = ledger.read_bytes()
    first = CAL.calibration_report(ledger=ledger)
    second = CAL.calibration_report(ledger=ledger)
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == \
        json.dumps(second, ensure_ascii=False, sort_keys=True)
    assert [b["bucket"] for b in first["reliability"]] == list(CAL.BUCKET_LABELS)
    assert [b["count"] for b in first["reliability"]] == [0, 1, 1, 1, 0]
    assert ledger.read_bytes() == before


def test_report_structure_and_json_ready(tmp_path):
    """报告字段齐全、计数自洽、可 JSON 序列化（供 API/页面直用）。"""
    rows = [_crow(0.65, 1, "d1-a"), _crow(0.85, 0, "d1-b"), {"confidence": 0.3}]
    rep = CAL.calibration_report(ledger=_ledger(tmp_path, rows))
    required = {"schema", "ledger", "readonly", "total", "measured", "unmeasured",
                "invalid", "min_measured", "overall_brier", "reliability", "status"}
    assert required <= set(rep)
    assert rep["schema"] == CAL.SCHEMA
    assert rep["status"] in {CAL.STATUS_UNMEASURED, CAL.STATUS_INSUFFICIENT,
                             CAL.STATUS_MEASURED}
    assert rep["total"] == 3
    assert rep["measured"] == 2 and rep["unmeasured"] == 1
    assert rep["measured"] + rep["unmeasured"] + rep["invalid"] == rep["total"]
    assert len(rep["reliability"]) == 5
    for bucket in rep["reliability"]:
        assert {"bucket", "lo", "hi", "count", "mean_confidence",
                "win_rate", "brier"} <= set(bucket)
    assert json.loads(json.dumps(rep)) == rep
    assert "D1 置信度校准" in CAL.render_text(rep)


def test_cli_json_readonly(tmp_path):
    """--json CLI 可用、可解析，且不写生产账/不新建缺失账本。"""
    rows = [_crow(0.8, 1, "d1-a"), _crow(0.6, 0, "d1-b")]
    ledger = _ledger(tmp_path, rows)
    before = ledger.read_bytes()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(ROOT / "agent_platform" / "d1_calibration.py"),
         "--json", "--ledger", str(ledger)],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace", env=env, timeout=120)
    assert proc.returncode == 0, proc.stderr
    rep = json.loads(proc.stdout)
    assert rep["status"] == CAL.STATUS_INSUFFICIENT
    assert rep["measured"] == 2
    assert rep["overall_brier"] == 0.2
    assert ledger.read_bytes() == before

    missing = tmp_path / "no_such_ledger.jsonl"
    proc2 = subprocess.run(
        [sys.executable, "-X", "utf8", str(ROOT / "agent_platform" / "d1_calibration.py"),
         "--json", "--ledger", str(missing)],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace", env=env, timeout=120)
    assert proc2.returncode == 0, proc2.stderr
    rep2 = json.loads(proc2.stdout)
    assert rep2["status"] == CAL.STATUS_UNMEASURED
    assert rep2["overall_brier"] is None
    assert not missing.exists()
