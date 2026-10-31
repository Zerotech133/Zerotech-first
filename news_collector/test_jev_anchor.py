# -*- coding: utf-8 -*-
"""JEV anchor 派生契约（WO-INTEL-COMPANIES-AUDIT）：不改 TypeSafe 题，只派生输出。"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import news_ai  # noqa: E402


def test_single_stock_anchor():
    d = news_ai._derive_anchor({"companies": ["300750"], "scope": "cn"})
    assert d["anchor"] == {"type": "stock", "code": "300750", "name": ""}


def test_basket_anchor():
    d = news_ai._derive_anchor({"companies": ["300059", "300136"], "scope": "cn"})
    assert d["anchor"]["type"] == "stock_basket"
    assert d["anchor"]["codes"] == ["300059", "300136"]


def test_commodity_fx_crypto_market_anchor():
    assert news_ai._derive_anchor({"companies": [], "scope": "commodity"})["anchor"]["type"] == "commodity"
    assert news_ai._derive_anchor({"companies": [], "scope": "fx"})["anchor"]["type"] == "fx"
    assert news_ai._derive_anchor({"companies": [], "scope": "crypto"})["anchor"]["type"] == "crypto"
    assert news_ai._derive_anchor({"companies": [], "scope": "cn"})["anchor"] == {
        "type": "market", "code": "cn", "name": ""}


def test_no_anchor():
    assert news_ai._derive_anchor({"companies": [], "scope": ""})["anchor"] is None
