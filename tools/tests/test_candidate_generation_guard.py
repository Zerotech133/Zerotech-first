# -*- coding: utf-8 -*-
"""候选池生成层门测试（WO-CAND-GEN-GUARD-20261031 / WO-GUARD-PROVENANCE-20261101）。

Phase 2 扩展（本单）：G3 增加 RSI 方法/复权口径同源；G4 增加完整日线×异常量比；
normalize_card 增加 bar_asof/bar_source/complete_bar/price_adjust/rsi_method/
evidence_rsi_method。旧 13 用例保持不回归，新增 provenance 用例覆盖
pass/unknown/fail 三态与 CLI 退出码。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
import candidate_generation_guard as g  # noqa: E402

GUARD = REPO / "tools" / "candidate_generation_guard.py"


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
        # Provenance（WO-GUARD-PROVENANCE-20261101）：默认口径齐全
        "bar_asof": "2026-10-30", "bar_source": "tdx_daily",
        "complete_bar": True, "price_adjust": "qfq",
        "rsi_method": "SMA14", "evidence_rsi_method": "SMA14",
    }
    base.update(kw)
    return base


def _status(card, asof=None):
    return g.evaluate_card(card, asof=asof)["surface_status"]


# ══ 旧 13 用例（不回归） ═════════════════════════════════════════════
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
    out = subprocess.run([sys.executable, "-X", "utf8", str(GUARD),
                          "--input", str(p), "--json"], capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0
    rep = json.loads(out.stdout)
    assert rep["summary"]["cards"] == 1


def test_cli_bad_input_rc2(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{oops", encoding="utf-8")
    out = subprocess.run([sys.executable, "-X", "utf8", str(GUARD),
                          "--input", str(p)], capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 2


def test_cli_out_atomic(tmp_path):
    p = tmp_path / "cards.json"
    p.write_text(json.dumps([_clean()]), encoding="utf-8")
    outpath = tmp_path / "report.json"
    out = subprocess.run([sys.executable, "-X", "utf8", str(GUARD),
                          "--input", str(p), "--json", "--out", str(outpath)],
                         capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0
    assert outpath.is_file()
    assert json.loads(outpath.read_text(encoding="utf-8"))["summary"]["cards"] == 1


# ══ 新用例：Provenance 归一（bar_asof/bar_source/complete_bar/复权/RSI 方法） ══
def test_normalize_provenance_fields_and_aliases():
    c = g.normalize_card({
        "candidate_id": "c_alias",
        "bar_date": "2026-10-31",
        "bar_src": "xq",
        "bar_complete": "false",
        "adjust": "hfq",
        "rsi_calc_method": "EMA14",
        "display_rsi_method": "EMA14",
    })
    assert c["bar_asof"] == "2026-10-31"
    assert c["bar_source"] == "xq"
    assert c["complete_bar"] is False
    assert c["price_adjust"] == "hfq"
    assert c["rsi_method"] == "EMA14"
    assert c["evidence_rsi_method"] == "EMA14"

    missing = g.normalize_card({"candidate_id": "c_missing"})
    assert missing["bar_asof"] == ""
    assert missing["bar_source"] == ""
    assert missing["complete_bar"] is None
    assert missing["price_adjust"] == ""
    assert missing["rsi_method"] == ""
    assert missing["evidence_rsi_method"] == ""


def test_gate_source_rsi_method_mismatch_fail():
    res = g.gate_source(g.normalize_card(
        _clean(rsi_method="SMA14", evidence_rsi_method="EMA14")))
    assert res["status"] == "fail"
    assert "rsi_method" in res["reason"]
    # 大小写/空白不敏感：sma 14 == SMA14
    ok = g.gate_source(g.normalize_card(
        _clean(rsi_method="sma 14", evidence_rsi_method="SMA14")))
    assert ok["status"] == "pass"


def test_gate_source_rsi_method_missing_unknown():
    em_missing = g.gate_source(g.normalize_card(_clean(evidence_rsi_method="")))
    assert em_missing["status"] == "unknown"
    assert "evidence_rsi_method" in em_missing["reason"]
    rm_missing = g.gate_source(g.normalize_card(_clean(rsi_method="")))
    assert rm_missing["status"] == "unknown"
    assert "missing rsi_method" in rm_missing["reason"]


def test_gate_source_price_adjust_missing_unknown():
    res = g.gate_source(g.normalize_card(_clean(price_adjust="")))
    assert res["status"] == "unknown"
    assert "price_adjust" in res["reason"]
    assert g.gate_source(g.normalize_card(_clean(price_adjust="none")))["status"] == "pass"


def test_gate_source_unknown_aggregates_provenance_gaps():
    res = g.gate_source(g.normalize_card(
        _clean(price_adjust="", evidence_rsi_method="")))
    assert res["status"] == "unknown"
    assert "price_adjust" in res["reason"]
    assert "evidence_rsi_method" in res["reason"]


# ══ 新用例：G4 完整日线 × 量比异常（fail / unknown / pass 三态） ════
def test_physiology_incomplete_bar_abnormal_volume_fail():
    low = g.gate_physiology(g.normalize_card(
        _clean(complete_bar=False, volume_ratio=0.0009)))
    assert low["status"] == "fail"
    assert "complete_bar=false" in low["reason"]
    assert "volume_ratio" in low["reason"]
    assert "bar_asof=2026-10-30" in low["reason"]
    high = g.gate_physiology(g.normalize_card(
        _clean(complete_bar=False, volume_ratio=250)))
    assert high["status"] == "fail"
    assert "complete_bar=false" in high["reason"]


def test_physiology_missing_complete_bar_abnormal_volume_unknown():
    low = g.gate_physiology(g.normalize_card(
        _clean(complete_bar=None, volume_ratio=0.0009)))
    assert low["status"] == "unknown"
    assert "missing complete_bar" in low["reason"]
    high = g.gate_physiology(g.normalize_card(
        _clean(complete_bar=None, volume_ratio=250)))
    assert high["status"] == "unknown"


def test_physiology_complete_bar_true_keeps_fail():
    # 旧语义保留：complete_bar=true + 量比异常仍 fail
    res = g.gate_physiology(g.normalize_card(
        _clean(complete_bar=True, volume_ratio=0.0009)))
    assert res["status"] == "fail"
    assert "complete_bar" not in res["reason"]


def test_physiology_incomplete_bar_normal_volume_pass():
    res = g.gate_physiology(g.normalize_card(
        _clean(complete_bar=False, volume_ratio=1.2)))
    assert res["status"] == "pass"


# ══ 新用例：evaluate 暴露 provenance / checks 契约 / CLI 退出码 ═══════
def test_evaluate_normalized_has_provenance():
    normalized = g.evaluate_card(_clean())["normalized"]
    assert normalized["bar_asof"] == "2026-10-30"
    assert normalized["bar_source"] == "tdx_daily"
    assert normalized["complete_bar"] is True
    assert normalized["price_adjust"] == "qfq"
    assert normalized["rsi_method"] == "SMA14"
    assert normalized["evidence_rsi_method"] == "SMA14"


def test_checks_keep_gate_status_reason():
    checks = g.evaluate_card(_clean(complete_bar=False, volume_ratio=0.0009))["checks"]
    assert [c["gate"] for c in checks] == ["G1", "G2", "G3", "G4", "G5"]
    for check in checks:
        assert {"gate", "status", "reason"} <= set(check)
        assert check["status"] in ("pass", "fail", "unknown")


def test_cli_provenance_gate_exit_codes(tmp_path):
    # fail：RSI 方法不同源 → rc=1
    fail_path = tmp_path / "fail.json"
    fail_path.write_text(json.dumps([_clean(rsi_method="SMA14",
                                            evidence_rsi_method="EMA14")]),
                         encoding="utf-8")
    fail_run = subprocess.run([sys.executable, "-X", "utf8", str(GUARD),
                               "--input", str(fail_path), "--json"],
                              capture_output=True, text=True, encoding="utf-8")
    assert fail_run.returncode == 1
    assert json.loads(fail_run.stdout)["summary"]["status"] == "fail"

    # unknown：缺 complete_bar + 量比异常 → rc=2
    unknown_path = tmp_path / "unknown.json"
    unknown_path.write_text(json.dumps([_clean(complete_bar=None,
                                               volume_ratio=0.0009)]),
                            encoding="utf-8")
    unknown_run = subprocess.run([sys.executable, "-X", "utf8", str(GUARD),
                                  "--input", str(unknown_path), "--json"],
                                 capture_output=True, text=True, encoding="utf-8")
    assert unknown_run.returncode == 2
    assert json.loads(unknown_run.stdout)["summary"]["status"] == "unknown"

    # pass：provenance 齐全 → rc=0
    pass_path = tmp_path / "pass.json"
    pass_path.write_text(json.dumps([_clean()]), encoding="utf-8")
    pass_run = subprocess.run([sys.executable, "-X", "utf8", str(GUARD),
                               "--input", str(pass_path), "--json"],
                              capture_output=True, text=True, encoding="utf-8")
    assert pass_run.returncode == 0
    assert json.loads(pass_run.stdout)["summary"]["status"] == "pass"
