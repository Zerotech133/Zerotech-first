# -*- coding: utf-8 -*-
"""D1 ↔ Hermes stock-data 桥测试（WO-D1-HERMES-BRIDGE-20261030）。"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "agent_platform" / "engine"))
from agent_platform import hermes_bridge as hb  # noqa: E402


def test_context_text_compacts_risk_and_signal(monkeypatch):
    risk = {"ok": True, "market_gate": {"action": "pass", "score": 0.2, "reason": "健康"},
            "risk": {"rules": {"R1": {"pass": 10, "block": 1}}}}
    sig = {"ok": True, "count": 50, "signals": [
        {"symbol": "600519.SH", "action": "buy", "score": 99, "confidence": 0.8, "rationale": "1买"},
        {"symbol": "000001.SZ", "action": "watch", "score": 70, "confidence": 0.5, "rationale": ""},
    ]}
    monkeypatch.setattr(hb, "_load", lambda force=False: (risk, sig))
    text = hb.context_text("600519")
    assert "市场闸" in text and "pass" in text
    assert "本票Hermes信号" in text and "600519" not in text  # 只给信号内容，不重复代码
    assert "今日信号总数: 50" in text


def test_disabled_returns_empty(monkeypatch):
    monkeypatch.setenv("D1_HERMES_BRIDGE", "0")
    assert hb.enabled() is False
    assert hb.context_text("600519") == ""


def test_make_context_appends_bridge(monkeypatch):
    import tools as engine_tools
    monkeypatch.setattr(engine_tools, "kline_daily", lambda code, n=30: [
        ["2026-10-30", 10, 10.5, 10.6, 9.9, 100]] * 30)
    monkeypatch.setattr(engine_tools, "quote_snapshot", lambda code: {
        "name": "测试", "price": 10.5, "change_pct": 1.0, "turnover_pct": 1.0,
        "pe": 10, "pb": 1, "mktcap_yi": 100})
    import agent_platform.hermes_bridge as hb2
    monkeypatch.setattr(hb2, "context_text", lambda code=None: "HERMES_TEST")
    out = engine_tools.make_context("600519")
    assert "HERMES_TEST" in out["market_technical"]
    assert "HERMES_TEST" in out["final_decision"]
