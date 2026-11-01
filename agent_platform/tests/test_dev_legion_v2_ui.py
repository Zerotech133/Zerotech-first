# -*- coding: utf-8 -*-
"""曹操军团指挥台 v2 页面静态契约（三件套/只读/四区/短屏）。"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HTML = ROOT / "portal" / "dev_legion_v2.html"
CSS = ROOT / "portal" / "static" / "dev_legion_v2.css"
JS = ROOT / "portal" / "static" / "dev_legion_v2.js"


def test_three_piece_and_short_screen():
    txt = HTML.read_text(encoding="utf-8")
    assert "/static/tokens.css" in txt
    assert "/static/nav.js" in txt
    assert "/static/shortscreen.js" in txt
    assert len(re.findall(r"<h2[ >]", txt)) >= 6


def test_live_api_readonly():
    txt = HTML.read_text(encoding="utf-8")
    js = JS.read_text(encoding="utf-8")
    assert "fetch(API_V2" in js
    assert 'const API_V2 = "/api/dev/legion"' in js
    assert "workorder_bus" in js
    assert "no-store" in js
    assert "只读" in txt
    assert "不写" in txt or "不派单" in txt


def test_four_zones():
    txt = HTML.read_text(encoding="utf-8")
    for zone in ("zone-bing", "zone-zhang", "zone-men", "zone-ling"):
        assert 'id="' + zone + '"' in txt
    for ch in ("兵", "仗", "门", "令"):
        assert ch in txt


def test_no_hardcoded_hex_in_page_css():
    css = CSS.read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3,8}", css), "页面 CSS 禁止自绘 hex 色"


def test_nav_entry_and_v1_link():
    nav = (ROOT / "portal" / "static" / "nav.js").read_text(encoding="utf-8")
    assert "/dev_legion_v2.html" in nav
    v1 = (ROOT / "portal" / "dev_legion.html").read_text(encoding="utf-8")
    assert "/dev_legion_v2.html" in v1
