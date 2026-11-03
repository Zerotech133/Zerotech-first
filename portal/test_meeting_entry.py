# -*- coding: utf-8 -*-
"""指挥部会议主系统入口：/meeting 页面与 8796 API 路由静态断言。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NGINX = ROOT / "portal" / "nginx.conf"
MEETING = ROOT / "agent_platform" / "legion_command" / "static" / "meeting.html"
HQ = ROOT / "portal" / "hq.html"


def test_meeting_entry_is_protected():
    s = NGINX.read_text(encoding="utf-8")
    assert "location = /meeting {" in s
    assert "proxy_pass http://$h:8796/static/meeting.html;" in s
    # 页面入口在 auth_request 保护下
    block = s.split("location = /meeting {", 1)[1].split("}", 1)[0]
    assert "auth_request /_auth_guard;" in block


def test_meeting_api_routes_to_8796_and_rewrites_agenda():
    s = NGINX.read_text(encoding="utf-8")
    assert "location ^~ /api/hq/meeting/ {" in s
    block = s.split("location ^~ /api/hq/meeting/ {", 1)[1].split("}", 1)[0]
    assert "proxy_pass http://$h:8796;" in block
    assert "auth_request /_auth_guard;" in block
    assert "/api/hq/meeting/agenda/message" in block
    assert "/api/hq/agenda" in block


def test_meeting_page_uses_five_seat_presets_and_hq_save():
    s = MEETING.read_text(encoding="utf-8")
    assert "hqPreset('morning')" in s
    assert "hqPreset('operation')" in s
    assert "hqPreset('acceptance')" in s
    assert "hqSave()" in s
    assert "hqDispatch()" in s
    assert "'A0','AI1','ZHAOYUN','GUANYU','HUANGZHONG'" in s


def test_meeting_page_uses_meeting_agenda_proxy():
    s = MEETING.read_text(encoding="utf-8")
    assert "fetch('/api/hq/meeting/agenda/message'" in s
    assert "fetch('/api/hq/meeting/agenda')" in s
    assert "fetch('/api/hq/agenda')" not in s


def test_hq_has_meeting_entry_link():
    s = HQ.read_text(encoding="utf-8")
    assert 'href="/meeting"' in s
    assert "作战会议室" in s


def test_meeting_chat_route_is_proxied_and_protected():
    s = NGINX.read_text(encoding="utf-8")
    assert "location = /api/chat {" in s
    block = s.split("location = /api/chat {", 1)[1].split("}", 1)[0]
    assert "auth_request /_auth_guard;" in block
    assert "proxy_pass http://$h:8796;" in block


def test_meeting_room_reflects_new_org_leads():
    s = MEETING.read_text(encoding="utf-8")
    for lead in ("DATA1", "PM1", "QA1", "DEV3", "MKT1", "LEG1", "LUSU1"):
        assert lead + ":{nm:" in s or lead + ":{ nm:" in s
    assert "const HQ_PRESET" in s
    assert "'D1','ARCH1','DATA1','INTEL0','QA1'" in s
    assert "部门负责人池" in s
