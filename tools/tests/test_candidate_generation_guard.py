# -*- coding: utf-8 -*-
"""候选池生成层门测试（WO-CAND-GEN-GUARD-20261031）。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
import candidate_generation_guard as g  # noqa: E402


def _clean(**kw):
    base = {
        "candidate_id": "c_clean", "market": "CN", "strategy": "chan_monitor",
        "signal_type": "3买", "center_low": 2.50, "center_high": 2.67,
        "close": 3.02, "trigger_close": 2.80, "trigger_date": "2026-10-28",
        "asof": "2026-10-30", "strategy_timeframe": "1d",
        "evidence_timeframe": "1d", "rule_rsi_period": 14,
        "evidence_rsi_period": 14, "rsi14": 38.0, "volume_ratio": 1.2,
        "daily_return": 1.5, "ret20": -5.3, "dist_low": 4.2,
        "pre_close": 2.97, "score": 60.0, "score_method": "percentile",
        "raw_momentum": None, "corporate_action": True,
        "rationale": "close 3.02 > center 2.67; vol 1.2",
    }
    base.update(kw)
    return base


def _status(card, asof=None):
    return g.evaluate_card(card, asof=asof)["surface_status"]


def test_clean_active_pass():
    c = _clean()
    assert g.gate_chan_geometry(g.normalize_card(c))["status"] == "pass"
    assert _status(c) == "pass"


def test_broken_reclaimed_expired():
    assert g.gate_chan_geometry(g.normalize_card(_clean(close=2.40)))["status"] == "fail"
    assert g.gate_chan_geometry(g.normalize_card(_clean(close=2.60)))["status"] == "fail"
    assert g.gate_chan_geometry(g.normalize_card(_clean(center_low=5.36, center_high=6.48,
                                                        close=21.62, trigger_close=6.0)))["status"] == "fail"
    assert g.gate_chan_geometry(g.normalize_card(_clean(trigger_close=2.50)))["status"] == "fail"


def test_geometry_missing_unknown():
    c = _clean(center_low=None, center_high=None)
    assert g.gate_chan_geometry(g.normalize_card(c))["status"] == "unknown"


def test_staleness():
    assert g.gate_staleness(g.normalize_card(_clean(trigger_date="2026-10-01")), "2026-10-30")["status"] == "fail"
    assert g.gate_staleness(g.normalize_card(_clean(close=3.5)), "2026-10-30")["status"] == "fail"


def test_source_mismatch():
    assert g.gate_source(g.normalize_card(_clean(strategy_timeframe="5m")))["status"] == "fail"
    assert g.gate_source(g.normalize_card(_clean(rule_rsi_period=6)))["status"] == "fail"


def test_physiology():
    assert g.gate_physiology(g.normalize_card(_clean(score=100.0)))["status"] == "fail"
    assert g.gate_physiology(g.normalize_card(_clean(volume_ratio=0.0009)))["status"] == "fail"
    assert g.gate_physiology(g.normalize_card(_clean(score=9.7, raw_momentum=9.66,
                                                     score_method="")))["status"] == "fail"


def test_corporate_big_move_unknown():
    assert g.gate_corporate(g.normalize_card(_clean(daily_return=17.85, corporate_action=None)))["status"] == "unknown"
    assert g.gate_corporate(g.normalize_card(_clean(daily_return=17.85, corporate_action=True)))["status"] == "pass"
    assert g.gate_corporate(g.normalize_card(_clean(daily_return=5.0)))["status"] == "pass"


def test_pool_score_saturation_and_volume_constant():
    cards = [_clean(candidate_id="c%d" % i, score=100.0, volume_ratio=0.0009)
             for i in range(3)]
    rep = g.evaluate(cards, asof="2026-10-30")
    stamps = {x["stamp"] for x in rep["pool_stamps"]}
    assert "score_saturation" in stamps
    assert "volume_ratio_constant" in stamps
    assert rep["summary"]["status"] == "fail"


def test_pool_same_factor_and_barbell():
    cards = [
        _clean(candidate_id="a", close=2.55, dist_low=1.0, ret20=-30.0, signal_type="watch"),
        _clean(candidate_id="b", close=2.55, dist_low=2.0, ret20=-25.0, signal_type="watch"),
        _clean(candidate_id="c", close=2.55, dist_low=3.0, ret20=+30.0, signal_type="watch"),
    ]
    stamps = {x["stamp"] for x in g.evaluate(cards, asof="2026-10-30")["pool_stamps"]}
    assert "same_factor_oversold" in stamps
    assert "barbell_extremes" in stamps


def test_crypto_quota_waste():
    cards = [_clean(candidate_id="x", market="CRYPTO", score=9.7, raw_momentum=9.66,
                    score_method="", volume_ratio=0.0009,
                    strategy_timeframe="5m", evidence_timeframe="1d")]
    stamps = {x["stamp"] for x in g.evaluate(cards)["pool_stamps"]}
    assert "quota_waste" in stamps


def test_cli_json_and_exit(tmp_path):
    p = tmp_path / "cards.json"
    p.write_text(json.dumps({"cards": [_clean()]}), encoding="utf-8")
    out = subprocess.run([sys.executable, "-X", "utf8", str(REPO / "tools" / "candidate_generation_guard.py"),
                          "--input", str(p), "--json"], capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0
    rep = json.loads(out.stdout)
    assert rep["summary"]["cards"] == 1


def test_cli_bad_input_rc2(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{oops", encoding="utf-8")
    out = subprocess.run([sys.executable, "-X", "utf8", str(REPO / "tools" / "candidate_generation_guard.py"),
                          "--input", str(p)], capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 2


def test_cli_out_atomic(tmp_path):
    p = tmp_path / "cards.json"
    p.write_text(json.dumps([_clean()]), encoding="utf-8")
    outpath = tmp_path / "report.json"
    out = subprocess.run([sys.executable, "-X", "utf8", str(REPO / "tools" / "candidate_generation_guard.py"),
                          "--input", str(p), "--json", "--out", str(outpath)],
                         capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0
    assert outpath.is_file()
    assert json.loads(outpath.read_text(encoding="utf-8"))["summary"]["cards"] == 1
