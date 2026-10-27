# -*- coding: utf-8 -*-
"""P2 /picks 默认精简契约。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "portal" / "picks.html"
CSS = ROOT / "portal" / "static" / "picks_simple.css"
JS = ROOT / "portal" / "static" / "picks_simple.js"


def test_picks_simple_assets():
    s = PAGE.read_text(encoding="utf-8")
    assert "/static/picks_simple.css" in s
    assert "/static/picks_simple.js" in s
    assert 'id="p2-bar"' not in s  # runtime injected, not static


def test_picks_simple_behavior():
    s = JS.read_text(encoding="utf-8")
    assert "MAX = 20" in s
    assert "showAll" in s
    assert "T1" in s and "T2" in s
    assert "#p2-toggle" in s
    css = CSS.read_text(encoding="utf-8")
    assert "#p2-bar" in css
