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


# ── 2026-10-30 JEVHERMES：jev_digest / jev_risk ──────────────────────
_CAL = {
    "data_through": "20261029", "stale_trade_days": 1, "n_trade_days": 13,
    "directional": {"n": 9793, "rate": 0.521, "baseline_all_bull": {"rate": 0.471}},
    "assessment": {
        "directional": {"edge_vs_baseline": 0.05, "gate_pass": False},
        "low_signal_event_types": ["price"],
    },
}
_DATA = {
    "window_hours": 24, "schema_version": "jev.v2",
    "summary_window": {"total": 10, "bull": 4, "bear": 3, "neutral": 3},
    "items": [
        {"ts": "2026-10-30 14:00:00", "bias": "bear", "bias_cn": "利空",
         "impact": "high", "impact_cn": "高", "title": "某公司被立案调查",
         "event_cn": "风险", "codes": ["600519"], "conf": 0.95,
         "horizon": "days", "low_signal": False,
         "posterior": {"excess": -0.02, "hit": 1}},
        {"ts": "2026-10-30 15:00:00", "bias": "bull", "bias_cn": "利多",
         "impact": "medium", "impact_cn": "中", "title": "中标大单",
         "event_cn": "其他", "codes": [], "conf": 0.6,
         "horizon": "unknown", "low_signal": False, "posterior": None},
    ],
}


def test_fmt_digest_contents():
    text = mcp._fmt_digest(_DATA, _CAL, "all", 8)
    assert "JEV 摘要" in text and "schema=jev.v2" in text
    assert "校准" in text and "门控=False" in text and "20261029" in text
    assert "低信号事件" in text and "price" in text
    assert "某公司被立案调查" in text and "后验" in text and "周期=days" in text


def test_digest_dispatch_and_validation(monkeypatch):
    seen = {}

    def fake_http(path, params):
        seen.update(params)
        return _DATA

    monkeypatch.setattr(mcp, "_http_get_json", fake_http)
    monkeypatch.setattr(mcp, "_load_calibration", lambda: _CAL)
    r = _call("jev_digest", {"hours": 24, "focus": "risk", "top": 5})
    assert "isError" in r["result"] and r["result"]["isError"] is False
    assert seen.get("bias") == "bear" and seen.get("with_posterior") == "1"
    # 参数校验
    assert _call("jev_digest", {"focus": "long"})["error"]["code"] == -32602
    assert _call("jev_digest", {"top": 99})["error"]["code"] == -32602
    assert _call("jev_digest", {"bogus": 1})["error"]["code"] == -32602
    # jev_risk 默认 hours=24/top=10，bias=bear
    r2 = _call("jev_risk", {})
    assert r2["result"]["isError"] is False and seen.get("bias") == "bear"
    assert _call("jev_risk", {"focus": "all"})["error"]["code"] == -32602


def test_tools_list_contains_digest_risk():
    r = mcp._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in r["result"]["tools"]}
    assert {"jev_query", "jev_calibration", "jev_digest", "jev_risk"} <= names
