# -*- coding: utf-8 -*-
"""分析总览 payload 单测（2026-10-26 统一分格第一批）。"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import m_analysis as A  # noqa: E402


def test_payload_structure(monkeypatch):
    monkeypatch.setattr(A, "_env_stats", lambda: ({"latest": "20261023"}, [
        {"label": "A股", "value": "21", "sub": "x", "level": "bad"}]))
    monkeypatch.setattr(A, "_html_text", lambda p, limit=1400, ttl=180: "盘前文本" if p == "/premarket" else "盘后文本")
    monkeypatch.setattr(A, "_sellpoint", lambda: (
        {"total": 2, "1卖": 1, "2卖": 1, "3卖": 0, "held": 1},
        [{"left": "A 000001", "sub": "1卖", "right": ""}]))
    monkeypatch.setattr(A, "_nine_dim", lambda: (
        {"generated_at": "2026-10-25T06:50:09+08:00", "n_buy": 0, "n_alert": 0},
        {"候选池(≤400)": 336},
        [{"left": "B 300307", "sub": "纺织 · RPS 97", "right": "+20.0%"}]))
    monkeypatch.setattr(A, "_resonance", lambda: (
        {"theme": "人形机器人", "window_days": 30, "n_full": 4,
         "generated_at": "2026-10-25 08:57:26"},
        [{"left": "C 603667", "sub": "结构 · 传导", "right": "全共振"}]))
    d = A.analysis_payload()
    assert d["ok"] is True and d["scene"] == "analysis"
    types = [b["type"] for b in d["blocks"]]
    assert types.count("stats") == 1
    assert types.count("list") == 3
    assert types.count("md") == 2
    assert types.count("note") == 1
    assert "盘前文本" in str(d["blocks"])
    assert "盘后文本" in str(d["blocks"])


def test_html_text_offline(monkeypatch):
    """8091 不可达时返回空串而不是抛异常。"""
    monkeypatch.setattr(A, "LOCAL", "http://127.0.0.1:1")
    A._CACHE.clear()
    assert A._html_text("/premarket", limit=10) == ""


def test_sellpoint_parser(monkeypatch, tmp_path):
    p = tmp_path / "sellpoint_cards.jsonl"
    p.write_text(
        '{"name":"A","code":"000001.SZ","type":"1卖","held":true,'
        '"detect_date":"2026-10-13","price":18.9,"tbe":false}\n'
        '{"name":"B","code":"000002.SZ","type":"2卖","held":false,'
        '"detect_date":"2026-10-14","price":9.9,"tbe":true}\n',
        encoding="utf-8")
    monkeypatch.setattr(A, "DATA", str(tmp_path))
    stats, items = A._sellpoint()
    assert stats["total"] == 2 and stats["1卖"] == 1 and stats["held"] == 1
    assert items[0]["left"].startswith("B")      # 按 detect_date 倒序
    assert items[0]["right"] == "TBE"
