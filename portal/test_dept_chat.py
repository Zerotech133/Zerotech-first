# -*- coding: utf-8 -*-
"""部门点对点对话页：路由/静态契约。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NGINX = ROOT / "portal" / "nginx.conf"
PAGE = ROOT / "portal" / "dept_chat.html"
BRIEF = ROOT / "portal" / "briefing.html"


def test_dept_chat_route_is_protected():
    s = NGINX.read_text(encoding="utf-8")
    assert "location = /dept-chat {" in s
    block = s.split("location = /dept-chat {", 1)[1].split("}", 1)[0]
    assert "auth_request /_auth_guard;" in block
    assert "try_files /dept_chat.html =404;" in block


def test_dept_chat_page_has_point_to_point_chat():
    s = PAGE.read_text(encoding="utf-8")
    assert "点对点对话" in s
    assert "hq_dept_chat_" in s
    assert "fetch('/api/chat'" in s
    assert "hq_human_brief_" in s
    assert "返回简报" in s
    assert ".wb-palette-trigger{display:none!important}" in s
    assert "hq_people.js" in s and "pLead" in s


def test_briefing_cards_link_to_dept_chat():
    s = BRIEF.read_text(encoding="utf-8")
    assert "/dept-chat?" in s
    assert "点对点对话" in s
    assert 'id="build"' in s and "v9" in s
