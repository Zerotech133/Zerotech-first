# -*- coding: utf-8 -*-
"""jev.calibration 健康卡检查单测（K-ORG-20261026-JEVCALHC）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import gen_health_card as hc  # noqa: E402


def _write_latest(root: Path, report: dict) -> None:
    d = root / "agent_platform" / "data" / "jev_audit"
    d.mkdir(parents=True, exist_ok=True)
    (d / "latest.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")


def _report(n=1997, rate=0.559, baseline=0.557, edge=0.002):
    return {
        "generated_at": "2026-10-26T17:00:00",
        "directional": {"n": n, "rate": rate,
                        "baseline_all_bull": {"n": n, "rate": baseline}},
        "assessment": {
            "directional": {"n": n, "accuracy": rate, "edge_vs_baseline": edge},
            "neutral": {"band_pct": 2.5, "band_hit_rate": 0.696, "highvol_share": 0.304},
            "bear": {"usable": False},
            "conf": {"decision_weight": False},
            "confidence": {"decision_weight": False},
        },
    }


def test_missing_file(tmp_path):
    cal = hc.collect_jev_calibration(tmp_path)
    assert cal["available"] is False
    level, detail = hc._jev_calibration_level(cal)
    assert level == "yellow" and "暂不可用" in detail


def test_parse_report(tmp_path):
    _write_latest(tmp_path, _report())
    cal = hc.collect_jev_calibration(tmp_path)
    assert cal["available"] is True
    assert cal["directional"]["n"] == 1997
    assert cal["baseline_bull"] == 0.557
    assert cal["edge_vs_baseline"] == 0.002
    assert cal["band_pct"] == 2.5
    assert cal["bear_usable"] is False


def test_level_insufficient_samples(tmp_path):
    _write_latest(tmp_path, _report(n=100))
    level, detail = hc._jev_calibration_level(hc.collect_jev_calibration(tmp_path))
    assert level == "yellow" and "样本不足" in detail


def test_level_degraded_is_red(tmp_path):
    _write_latest(tmp_path, _report(n=500, rate=0.45, edge=-0.10))
    level, detail = hc._jev_calibration_level(hc.collect_jev_calibration(tmp_path))
    assert level == "red" and "模型退化" in detail


def test_level_edge_is_green(tmp_path):
    _write_latest(tmp_path, _report(n=1997, rate=0.62, baseline=0.55, edge=0.07))
    level, _ = hc._jev_calibration_level(hc.collect_jev_calibration(tmp_path))
    assert level == "green"


def test_level_no_edge_is_yellow(tmp_path):
    _write_latest(tmp_path, _report())
    level, detail = hc._jev_calibration_level(hc.collect_jev_calibration(tmp_path))
    assert level == "yellow" and "无显著增量" in detail
# ── 2026-10-30 BATCH1：门控/数据新鲜度/相对基线红 ──────────────────────
def _b1_report(**over):
    base = {
        "generated_at": "2026-10-30T07:10:00", "data_through": "20261030",
        "latest_trade_day": "20261030", "stale_trade_days": 0, "n_trade_days": 20,
        "directional": {"n": 3619, "rate": 0.554, "baseline_all_bull": {"n": 3619, "rate": 0.542}},
        "assessment": {"directional": {"n": 3619, "accuracy": 0.554,
                                       "edge_vs_baseline": 0.012,
                                       "wilson95_lower": 0.538, "gate_pass": True},
                       "neutral": {"band_pct": 2.5, "band_hit_rate": 0.684,
                                   "highvol_share": 0.316},
                       "bear": {"usable": False}, "bull": {"usable": False},
                       "confidence": {"decision_weight": False},
                       "low_signal_event_types": ["price"]},
    }
    base.update(over)
    return base


def test_gate_pass_required_for_green(tmp_path):
    _write_latest(tmp_path, _b1_report())
    cal = hc.collect_jev_calibration(tmp_path)
    level, detail = hc._jev_calibration_level(cal)
    assert level == "green", detail
    assert "20" in detail and "price" in detail


def test_stale_calibration_is_yellow(tmp_path):
    _write_latest(tmp_path, _b1_report(stale_trade_days=4, data_through="20261024"))
    cal = hc.collect_jev_calibration(tmp_path)
    level, detail = hc._jev_calibration_level(cal)
    assert level == "yellow" and "落后" in detail


def test_relative_baseline_red(tmp_path):
    rep = _b1_report()
    rep["directional"]["rate"] = 0.46
    rep["assessment"]["directional"]["gate_pass"] = False
    _write_latest(tmp_path, rep)
    cal = hc.collect_jev_calibration(tmp_path)
    level, detail = hc._jev_calibration_level(cal)
    assert level == "red" and "基线" in detail


def test_edge_ok_but_days_short_yellow(tmp_path):
    rep = _b1_report()
    rep["n_trade_days"] = 13
    rep["directional"]["rate"] = 0.60          # edge = +5.6pp ≥ 2pp
    rep["assessment"]["directional"]["edge_vs_baseline"] = 0.056
    rep["assessment"]["directional"]["wilson95_lower"] = 0.575   # > 基线 0.542
    rep["assessment"]["directional"]["gate_pass"] = False        # 仅天数不足
    _write_latest(tmp_path, rep)
    cal = hc.collect_jev_calibration(tmp_path)
    level, detail = hc._jev_calibration_level(cal)
    assert level == "yellow" and "交易日不足" in detail
