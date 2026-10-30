# -*- coding: utf-8 -*-
"""JEV 判读账本纯函数单测（K-DATA-20261026-JEVAUDIT）。"""
from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import jev_audit as ja  # noqa: E402


def test_norm_ts_code():
    assert ja.norm_ts_code("600000") == "600000.SH"
    assert ja.norm_ts_code("000001") == "000001.SZ"
    assert ja.norm_ts_code("300750") == "300750.SZ"
    assert ja.norm_ts_code("830799") == "830799.BJ"
    assert ja.norm_ts_code("920001") == "920001.BJ"          # 北交所新段
    assert ja.norm_ts_code("600000.SH") == "600000.SH"      # BATCH1：收后缀
    assert ja.norm_ts_code("000001.SZ") == "000001.SZ"
    assert ja.norm_ts_code("688411.SH") == "688411.SH"
    assert ja.norm_ts_code("") is None
    assert ja.norm_ts_code("12345") is None
    assert ja.norm_ts_code("ABC") is None


def test_next_trade_day():
    opens = ["20261022", "20261023", "20261026", "20261027"]
    assert ja.next_trade_day("20261022", opens) == "20261023"
    assert ja.next_trade_day("20261023", opens) == "20261026"  # 跳过周末
    assert ja.next_trade_day("20261024", opens) == "20261026"
    assert ja.next_trade_day("20261027", opens) is None  # 无后续交易日


def test_hit_of():
    assert ja.hit_of("bull", 0.8) == 1
    assert ja.hit_of("bull", -0.8) == 0
    assert ja.hit_of("bear", -0.8) == 1
    assert ja.hit_of("bear", 0.8) == 0
    assert ja.hit_of("neutral", 0.5) == 1
    assert ja.hit_of("neutral", -0.9) == 1
    assert ja.hit_of("neutral", 1.5) == 1      # 默认带宽 2.5%：1.5% 算中性
    assert ja.hit_of("neutral", 1.5, band=1.0) == 0  # 显式窄带宽下超出
    assert ja.hit_of("neutral", 2.6) == 0


def test_bucket_of():
    assert ja.bucket_of(0.50) == "[0.00,0.55)"
    assert ja.bucket_of(0.55) == "[0.55,0.65)"
    assert ja.bucket_of(0.90) == "[0.85,1.01)"
    assert ja.bucket_of(1.00) == "[0.85,1.01)"


def test_rate_and_wilson():
    r = ja.rate(3, 4)
    assert r["n"] == 4 and r["hits"] == 3 and r["rate"] == 0.75
    lo, hi = r["wilson95"]
    assert 0.0 <= lo <= 0.75 <= hi <= 1.0
    empty = ja.rate(0, 0)
    assert empty["rate"] is None and empty["wilson95"] == [0.0, 0.0]

def test_default_band_is_2_5():
    assert ja.DEFAULT_BAND == 2.5


def test_assess_report_flags():
    report = {
        "directional": {"n": 2000, "rate": 0.60,
                        "baseline_all_bull": {"n": 2000, "rate": 0.55}},
        "by_bias": {
            "bull": {"n": 1200, "accuracy": 0.62},
            "bear": {"n": 400, "accuracy": 0.50},
        },
        "neutral": {"n": 800, "band_hit_rate": 0.70, "highvol_share": 0.30},
        "scope": {"band_pct": 2.5},
    }
    a = ja.assess_report(report)
    assert a["bull"]["usable"] is True      # n>=1000 且 > 基线+2pp
    assert a["bear"]["usable"] is False     # 样本不足 1000
    assert a["neutral"]["usable_as_direction"] is False
    assert a["confidence"]["decision_weight"] is False
    assert a["directional"]["edge_vs_baseline"] == 0.05



def test_assess_event_type_low_signal():
    report = {
        "directional": {"n": 2000, "rate": 0.60,
                        "baseline_all_bull": {"n": 2000, "rate": 0.55}},
        "by_bias": {"bull": {"n": 1200, "accuracy": 0.62},
                    "bear": {"n": 400, "accuracy": 0.50}},
        "neutral": {"n": 800, "band_hit_rate": 0.70, "highvol_share": 0.30},
        "by_event_type": {
            "price": {"n": 342, "rate": 0.488},
            "other": {"n": 1348, "rate": 0.588},
            "tiny": {"n": 30, "rate": 0.40},
        },
        "scope": {"band_pct": 2.5},
    }
    a = ja.assess_report(report)
    assert a["low_signal_event_types"] == ["price"]
    assert a["event_types"]["price"]["use"] == "low_signal"
    assert "tiny" not in a["event_types"]      # 样本不足 100 不标记
    assert "other" not in a["low_signal_event_types"]
