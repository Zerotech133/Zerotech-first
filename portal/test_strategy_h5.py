# -*- coding: utf-8 -*-
"""策略工单 H5 单测（W43 环 3 工程落地）。"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import m_strategy as ms  # noqa: E402
import strategy_ring as sr  # noqa: E402


def test_strategy_payload(monkeypatch, tmp_path):
    monkeypatch.setattr(ms, "ORDERS", str(tmp_path / "orders.jsonl"))
    monkeypatch.setattr(ms, "SHADOW", str(tmp_path / "shadow.jsonl"))
    monkeypatch.setattr(sr, "ORDERS", str(tmp_path / "orders.jsonl"))
    monkeypatch.setattr(sr, "SHADOW", str(tmp_path / "shadow.jsonl"))
    oid = sr.create_order("均线多头排列，放量突破，RPS>90")["order"]["id"]
    sr._update(oid, status="rejected", reason="回测收益 -21.90% ≤ 0")
    d = ms.strategy_payload()
    assert d["ok"] is True and d["scene"] == "strategy"
    assert d["blocks"][0]["type"] == "stats"
    assert any(b["type"] == "table" for b in d["blocks"])
    assert set(d["windows"]) == {"1y", "40d", "3y"}
    table = [b for b in d["blocks"] if b["type"] == "table"][0]
    assert any("已拒绝" in r[3] for r in table["rows"])


def test_strategy_submit_validation(monkeypatch, tmp_path):
    monkeypatch.setattr(sr, "ORDERS", str(tmp_path / "orders.jsonl"))
    r = ms.submit("太短", "1y")
    assert r["ok"] is False and "10–4000" in r["error"]
    r2 = ms.submit("均线多头排列，放量突破，RPS>90，排除ST", "bad-window")
    assert r2["ok"] is False


def test_strategy_submit_spawns_worker(monkeypatch, tmp_path):
    monkeypatch.setattr(sr, "ORDERS", str(tmp_path / "orders.jsonl"))
    monkeypatch.setattr(ms, "WLOG", str(tmp_path / "worker"))
    calls = []

    class _FakePopen:
        def __init__(self, args, **kw):
            calls.append((args, kw))

    monkeypatch.setattr(ms.subprocess, "Popen", _FakePopen)
    r = ms.submit(
        "缠论二买，全市场选股池，top10，单票8%，持有最多10天",
        "1y",
        operator_open_id="ou_owner",
        token_hash="tok123",
    )
    assert r["ok"] is True and r["order_id"].startswith("STR-")
    assert calls and calls[0][0][1:3] == ["-X", "utf8"]
    row = sr._last_order(r["order_id"])
    assert row["source"] == "m_strategy"
    assert row["status"] == "received"
    # 频率限制：同 token_hash 30s 内第二次拒绝
    r2 = ms.submit(
        "缠论二买，全市场选股池，top10，单票8%",
        "1y",
        operator_open_id="ou_owner",
        token_hash="tok123",
    )
    assert r2["ok"] is False and "频繁" in r2["error"]
