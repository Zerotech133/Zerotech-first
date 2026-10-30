# -*- coding: utf-8 -*-
"""D1 校准门测试。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import d1_calibration as C  # noqa: E402


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
