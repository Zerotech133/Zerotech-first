# -*- coding: utf-8 -*-
"""作战台页面/路由测试（WO-WARROOM-LIVE-20261030）。"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
PORTAL = REPO / "portal"


def test_page_has_live_fetch():
    html = (PORTAL / "d1_war_room.html").read_text(encoding="utf-8")
    assert "/api/war-room/snapshot" in html
    assert "loadLive" in html
    assert "tokens.css" in html and "nav.js" in html


def test_route_registered():
    api = (PORTAL / "portal_api.py").read_text(encoding="utf-8")
    assert '"/api/war-room/snapshot"' in api
    assert "war_room_data" in api
