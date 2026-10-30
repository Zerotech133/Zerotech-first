# -*- coding: utf-8 -*-
"""D1 投研工作台页面/路由测试（WO-D1-RESEARCH）。"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
PORTAL = REPO / "portal"


def test_page_assets_and_fields():
    html = (PORTAL / "d1_research.html").read_text(encoding="utf-8")
    for token in ("/api/d1/research", "T1", "T2", "可买", "止损", "止盈", "交棒",
                  "/static/tokens.css", "/static/nav.js", "/static/shortscreen.js"):
        assert token in html, token
    assert "不管理策略" in html


def test_routes_registered():
    api = (PORTAL / "portal_api.py").read_text(encoding="utf-8")
    assert '"/api/d1/research"' in api
    assert '"/d1/research"' in api
    assert '"d1_research.html"' in api
