# -*- coding: utf-8 -*-
"""P1 /strategies 分组折叠契约。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = ROOT / "portal" / "api_backtest.py"
CSS = ROOT / "portal" / "static" / "deep_shell.css"


def test_strategies_grouped_cards():
    s = API.read_text(encoding="utf-8")
    assert "cards_by" in s
    assert "strat-group" in s
    assert "strat-open" in s
    assert "groups_html" in s


def test_strategies_folded_groups_and_sop():
    s = API.read_text(encoding="utf-8")
    assert "details class='strat-group'" in s
    assert "details class='strat-sop'" in s
    assert "strat-sop-body" in s
    css = CSS.read_text(encoding="utf-8")
    assert ".strategies-page .strat-group" in css
    assert ".strategies-page .strat-sop" in css
