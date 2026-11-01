# -*- coding: utf-8 -*-
"""acad1_v2_export 测试（WO-ACAD1-BRIDGE-EXPORT-20261101）。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
import acad1_v2_export as ex  # noqa: E402


def _pool_rows():
    return [
        {"code": "000001.SZ", "name": "A", "market": "CN", "strategy_id": "v1-cn-strat-4",
         "signal": "3买", "score": 100.0, "selection_reason": "中枢[10.0,11.0]", "data_date": "2026-10-31"},
        {"code": "000002.SZ", "name": "B", "market": "CN", "strategy_id": "v1-cn-strat-2",
         "signal": "均值回归", "score": 90.0, "selection_reason": "", "data_date": "2026-10-31"},
        {"code": "ETH/USDT", "name": "ETH", "market": "CRYPTO", "strategy_id": "v1-crypto-strat-11",
         "signal": "RSI", "score": 70.9, "selection_reason": "5分钟", "data_date": "2026-10-31"},
    ]


def _bars(last_volume=1000.0):
    bars = []
    for i in range(25):
        d = "2026-10-%02d" % (i + 1)
        bars.append({"trade_date": d, "high": 11.0 + i, "low": 9.0 + i,
                     "close": 10.0 + i, "volume": 1000.0, "pre_close": 9.5 + i})
    bars[-1]["volume"] = last_volume
    return bars


def _selection():
    return {
        ("CN", "000001.SZ"): {"max_score": 100.0, "source_strategies": "v1-cn-strat-4", "reason": "中枢[10.0,11.0]"},
        ("CN", "000002.SZ"): {"max_score": 90.0, "source_strategies": "v1-cn-strat-2", "reason": ""},
        ("CRYPTO", "ETH/USDT"): {"max_score": 70.9, "source_strategies": "v1-crypto-strat-11", "reason": "5分钟"},
    }


def test_percentile_rank():
    assert ex.percentile_rank([]) == []
    assert ex.percentile_rank([1.0]) == [0.0]
    p = ex.percentile_rank([10.0, 20.0, 20.0, 30.0])
    assert p[0] == 0.0 and p[3] == 1.0
    assert abs(p[1] - 0.5) < 1e-9 and p[1] == p[2]


def test_parse_center_and_signal():
    assert ex.parse_center("中枢[10.36,11.50](10笔)") == (10.36, 11.50)
    assert ex.parse_center("no center") == (None, None)
    assert ex.parse_signal_type("CHAN", "缠论三买") == "3买"
    assert ex.parse_signal_type("均值回归", "") == "均值回归"
    assert ex.parse_signal_type("趋势动量", "") == "趋势动量"


def test_build_cards_provenance_scores():
    bars = {("CN", "000001.SZ"): _bars(), ("CN", "000002.SZ"): _bars(),
            ("CRYPTO", "ETH/USDT"): _bars(last_volume=0.5)}
    cards = ex.build_cards(_pool_rows(), _selection(), bars, {}, "2026-10-31")
    assert len(cards) == 3
    by = {c["candidate_id"]: c for c in cards}
    assert by["000001.SZ"]["score"] != by["000002.SZ"]["score"]
    assert by["000001.SZ"]["score_method"] == "bridge_percentile_v1"
    assert by["000001.SZ"]["bar_source"] == "zhice_v2:v2_bar_daily"
    assert by["000001.SZ"]["price_adjust"] == "none"
    assert by["000001.SZ"]["rsi_method"] == "sma14"
    assert by["000001.SZ"]["center_low"] == 10.0
    assert by["000001.SZ"]["ma20"] is not None
    assert by["000001.SZ"]["rsi14"] is not None
    assert by["000001.SZ"]["bridge_export"] is True
    assert by["ETH/USDT"]["complete_bar"] is False
    assert by["ETH/USDT"]["strategy_timeframe"] == "5m"


def test_build_cards_pre_close_fallback():
    bars = _bars()
    bars[-1]["pre_close"] = None
    cards = ex.build_cards(_pool_rows()[:1], _selection(), {("CN", "000001.SZ"): bars}, {}, "2026-10-31")
    c = cards[0]
    assert c["prev_close_src"] == "prev_close"
    assert c["daily_return"] is not None


def test_cli_missing_dsn():
    r = subprocess.run([sys.executable, "-X", "utf8", str(REPO / "tools" / "acad1_v2_export.py"),
                        "--json"], capture_output=True, text=True, encoding="utf-8",
                       env={"PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 2
    assert "missing_env" in r.stdout


def test_bar_symbol_aliases():
    assert ex.bar_symbol_aliases("sh688004") == ["688004.SH"]
    assert ex.bar_symbol_aliases("sz000001") == ["000001.SZ"]
    assert ex.bar_symbol_aliases("000001.SZ") == ["000001.SZ"]
