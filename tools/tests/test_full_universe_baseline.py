# -*- coding: utf-8 -*-
"""全量选股 Phase A 基线工具测试（WO-STRAT-FULL-BASE-20261102）。离线纯函数。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "full_universe_baseline.py"
_spec = importlib.util.spec_from_file_location("full_universe_baseline", str(TOOL))
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


class Sig:
    def __init__(self, symbol, action, score=70.0, confidence=0.5, rationale="x"):
        self.symbol = symbol
        self.action = action
        self.score = score
        self.confidence = confidence
        self.rationale = rationale


def test_ymd8_validation():
    assert mod._ymd8("20261030") == "20261030"
    assert mod._ymd8("2026-10-30") == "20261030"
    assert mod._ymd8("2026/10/30") == "20261030"
    assert mod._ymd8("bad") == ""


def test_summarize_signals_counts():
    out = mod.summarize_signals([
        Sig("A", "buy"), Sig("A", "sell"), Sig("B", "buy"), Sig("C", "watch"),
    ])
    assert out["signal_count"] == 4
    assert out["symbol_count"] == 3
    assert out["buy_count"] == 2
    assert out["sell_count"] == 1
    assert out["watch_count"] == 1
    assert out["actions"] == {"buy": 2, "sell": 1, "watch": 1}


def test_summarize_empty():
    out = mod.summarize_signals([])
    assert out["signal_count"] == 0 and out["buy_count"] == 0
    assert out["actions"] == {}


def test_inventory_marks_classics_runnable():
    inv = mod.inventory()
    ids = {c["strategy_id"]: c for c in inv}
    assert len(ids) == 10
    for sid in mod.CLASSICS:
        assert ids[sid]["runnable"] is True
    assert ids["chan_monitor"]["runnable"] is True
    assert "kg_boost" not in ids  # adapter-only，无 strategy card
    assert ids["nine_dim"]["runnable"] is False


def test_render_markdown_structure():
    result = {
        "schema": mod.SCHEMA,
        "trade_date": "20261030",
        "universe": {"market": "CN", "count": 5631, "limited": False},
        "strategies": [
            {"strategy_id": "ma_cross", "status": "ok", "signal_count": 3,
             "buy_count": 2, "sell_count": 1, "watch_count": 0, "symbol_count": 3},
            {"strategy_id": "kg_boost", "status": "error", "signal_count": 0,
             "buy_count": 0, "sell_count": 0, "watch_count": 0, "symbol_count": 0,
             "error": "no graph"},
        ],
        "inventory": [{"strategy_id": "ma_cross", "runnable": True,
                       "scope": "full_universe_recompute"}],
        "notes": ["readonly"],
    }
    md = mod.render_markdown(result)
    assert "# 全量选股 Phase A 基线 20261030" in md
    assert "count=5631" in md
    assert "| ma_cross | ok | 3 | 2 | 1 | 0 | 3 |" in md
    assert "no graph" in md
    assert "只读" in md
