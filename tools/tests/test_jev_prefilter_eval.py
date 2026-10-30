# -*- coding: utf-8 -*-
"""prefilter 评估纯函数单测（PREFILTEREVAL）。"""
from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import jev_prefilter_eval as ev  # noqa: E402


def test_evaluate_counts_false_kill_and_capture():
    rows = [
        {"id": "a", "title": "开盘涨停！000678，6连板！", "source": "sina",
         "bias": "bull", "conf": 0.9, "event_type": "price"},
        {"id": "b", "title": "收盘：沪指涨 0.8% 报 3120 点", "source": "sina",
         "bias": "neutral", "conf": 0.9, "event_type": "other"},
        {"id": "c", "title": "公司收到证监会立案告知书", "source": "sina",
         "bias": "bear", "conf": 0.8, "event_type": "risk"},
        {"id": "d", "title": "关于召开股东大会的通知", "source": "announcement",
         "bias": "bull", "conf": 0.6, "event_type": "other"},
    ]
    rep = ev.evaluate(rows, 0.75, mode="aggressive")
    assert rep["n"] == 4 and rep["skip"] == 2
    assert rep["false_kill"] == 0          # 低置信 0.6 不算信号；涨停已被方向词保留
    assert rep["keep_signal"] == 2
    assert rep["signal_capture_rate"] == 1.0


def test_evaluate_detects_false_kill():
    rows = [
        {"id": "x", "title": "午盘播报：某公司涨价 10%", "source": "sina",
         "bias": "bull", "conf": 0.9, "event_type": "price"},
    ]
    rep = ev.evaluate(rows, 0.75)
    # 该标题若被判 skip（取决于规则），误杀应被统计；若 keep 则不应算误杀
    if rep["skip"]:
        assert rep["false_kill"] == rep["skip"]