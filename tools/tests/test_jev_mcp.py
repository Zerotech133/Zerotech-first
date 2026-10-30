# -*- coding: utf-8 -*-
"""JEV MCP 单测（BATCH3）：JSON-RPC 参数校验(-32602)、计数措辞、低信号消费。"""
from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import jev_mcp as mcp  # noqa: E402


def _call(name, args=None):
    return mcp._handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                        "params": {"name": name, "arguments": args or {}}})


def test_bad_hours_returns_32602():
    r = _call("jev_query", {"hours": "abc"})
    assert r["error"]["code"] == -32602
    assert "整数" in r["error"]["message"]


def test_hours_out_of_range_returns_32602():
    r = _call("jev_query", {"hours": 9999})
    assert r["error"]["code"] == -32602


def test_bad_code_returns_32602():
    r = _call("jev_query", {"code": "abc"})
    assert r["error"]["code"] == -32602


def test_unknown_arg_returns_32602():
    r = _call("jev_query", {"bogus": 1})
    assert r["error"]["code"] == -32602


def test_unknown_tool_returns_32602():
    r = _call("no_such_tool")
    assert r["error"]["code"] == -32602


def test_calibration_rejects_args():
    r = _call("jev_calibration", {"x": 1})
    assert r["error"]["code"] == -32602


def test_fmt_query_wording_and_low_signal():
    data = {
        "window_hours": 24, "total": 9, "returned": 2,
        "summary_window": {"total": 9, "bull": 4, "bear": 3, "neutral": 2},
        "low_signal_event_types": ["price"],
        "posterior_band_pct": None,
        "items": [
            {"ts": "2026-10-30 10:00:00", "bias": "bear", "bias_cn": "利空",
             "title": "某公司被处罚", "event_type": "policy", "event_cn": "政策",
             "impact": "high", "impact_cn": "高", "low_signal": False, "codes": []},
            {"ts": "2026-10-30 11:00:00", "bias": "bear", "bias_cn": "利空",
             "title": "油价上涨", "event_type": "price", "event_cn": "价格",
             "impact": "low", "impact_cn": "低", "low_signal": True, "codes": []},
        ],
    }
    text = mcp._fmt_query(data)
    assert "本次返回 2 条" in text
    assert "低信号事件" in text and "price" in text
    assert "[低信号事件]" in text


def test_fmt_calibration_gate_and_low_signal():
    cal = {
        "generated_at": "2026-10-30T07:10:00",
        "data_through": "20261023", "stale_trade_days": 5, "n_trade_days": 9,
        "directional": {"n": 3926, "rate": 0.555, "baseline_all_bull": {"rate": 0.54}},
        "assessment": {
            "directional": {"edge_vs_baseline": 0.015, "wilson95_lower": 0.539,
                            "gate_pass": False},
            "neutral": {"band_pct": 2.5, "band_hit_rate": 0.67, "highvol_share": 0.33},
            "bull": {"usable": False, "reason": "x"},
            "bear": {"usable": False, "reason": "y"},
            "confidence": {"decision_weight": False, "note": "z"},
            "low_signal_event_types": ["price"],
        },
    }
    text = mcp._fmt_calibration(cal)
    assert "门控通过=False" in text
    assert "20261023" in text
    assert "低信号事件" in text
