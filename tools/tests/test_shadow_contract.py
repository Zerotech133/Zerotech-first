# -*- coding: utf-8 -*-
"""组合/影子盘契约测试（轻量）。"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SHADOW = REPO / "portal" / "static" / "shadow_selection.json"
PORT = REPO / "research" / "reports" / "portfolio_20261030.json"


def test_shadow_payload():
    d = json.loads(SHADOW.read_text(encoding="utf-8"))
    assert d["days"] >= 5
    assert 0.5 <= d["nav"] <= 2.0
    assert d["mdd_pct"] <= 0
    assert isinstance(d["ledger"], list) and len(d["ledger"]) >= 5
    for r in d["ledger"]:
        assert {"date", "net", "nav"} <= set(r)


def test_portfolio_caps():
    d = json.loads(PORT.read_text(encoding="utf-8"))
    assert 1 <= d["n"] <= 30
    assert 0 <= d["cash"] <= 1
    for h in d["holdings"]:
        assert h["weight"] <= 0.051, (h["ts_code"], h["weight"])
    ind = d["industry_exposure"]
    assert all(v <= 0.301 for v in ind.values()), ind
    assert abs(sum(h["weight"] for h in d["holdings"]) + d["cash"] - 1) < 1e-6
