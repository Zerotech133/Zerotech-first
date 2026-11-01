# -*- coding: utf-8 -*-
"""司马徽研究台 /academy/shuijing 页面静态契约（只读/三件套/短屏）。"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HTML = ROOT / "portal" / "shuijing.html"
CSS = ROOT / "portal" / "static" / "shuijing.css"
JS = ROOT / "portal" / "static" / "shuijing.js"
NGINX = ROOT / "portal" / "nginx.conf"


def test_three_piece_and_short_screen():
    txt = HTML.read_text(encoding="utf-8")
    assert "/static/tokens.css" in txt
    assert "/static/nav.js" in txt
    assert "/static/shortscreen.js" in txt
    assert len(re.findall(r"<h2[ >]", txt)) >= 6


def test_readonly_and_api():
    txt = HTML.read_text(encoding="utf-8")
    js = JS.read_text(encoding="utf-8")
    assert 'fetch("/api/acad1/pool"' in js
    assert "no-store" in js
    assert "只读" in txt
    assert "不自动下单" in txt or "不接实盘" in txt
    for sid in ("kpi-pool", "pool-rows", "risk-list", "drawer", "error-banner"):
        assert 'id="' + sid + '"' in txt


def test_route_and_nav():
    nginx = NGINX.read_text(encoding="utf-8")
    assert "location = /academy/shuijing" in nginx
    assert "try_files /shuijing.html" in nginx
    nav = (ROOT / "portal" / "static" / "nav.js").read_text(encoding="utf-8")
    assert "/academy/shuijing" in nav


def test_no_hardcoded_hex():
    css = CSS.read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3,8}", css), "页面 CSS 禁止自绘 hex 色"
