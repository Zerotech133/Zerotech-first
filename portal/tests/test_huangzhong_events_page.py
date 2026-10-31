# -*- coding: utf-8 -*-
"""A2 事件 UI 页（portal/static/huangzhong_events.html）存在性与只读性守护测试。"""
from __future__ import annotations

from pathlib import Path

_PAGE = Path(__file__).resolve().parents[1] / "static" / "huangzhong_events.html"


def test_page_exists_and_frontend_trio():
    assert _PAGE.is_file(), "缺少只读页 portal/static/huangzhong_events.html"
    html = _PAGE.read_text(encoding="utf-8")
    assert "/static/tokens.css" in html
    assert "/static/nav.js" in html
    assert "viewport" in html


def test_page_readonly_and_fetches_huangzhong_events():
    html = _PAGE.read_text(encoding="utf-8")
    # 只读：只 GET 事件端点，无写方法
    assert "/api/huangzhong/events?limit=" in html
    assert "'POST'" not in html and '"POST"' not in html
    assert "method:'POST'" not in html and "method:'PUT'" not in html
    # 只读声明与观察模式口径
    assert "只读" in html
    assert "blocking=false" in html or "观察模式" in html
    # 状态区分
    for verdict in ("PASS", "OBSERVE", "REJECT"):
        assert verdict in html
