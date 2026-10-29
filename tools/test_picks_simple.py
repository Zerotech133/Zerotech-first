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
    assert "activeTier" in s
    assert "explicit" in s
    css = CSS.read_text(encoding="utf-8")
    assert "#p2-bar" in css


def test_filter_memory_contract():
    s = JS.read_text(encoding="utf-8")
    assert "localStorage" in s
    assert "zero.picks.filters.v2:" in s
    assert "restoreState" in s
    assert "resetState" in s
    assert "p2-reset" in s
    assert "advanced" in s and "showAll" in s


def test_memory_page_and_date_scope():
    s = JS.read_text(encoding="utf-8")
    assert "pageKey" in s
    assert "STORE_PREFIX" in s
    assert "dataDate" in s
    assert "stale" in s
    assert "日期变化，已重置" in s
    assert "重置本页筛选" in s
