# -*- coding: utf-8 -*-
"""A2 事件 UI 页（portal/static/huangzhong_events.html）存在性/只读性/增强元素守护测试。

覆盖（2026-10-31 WO-HZ-BRIEF-20261031 增强）：
  · 前端三件套（tokens.css / nav.js / viewport）与只读边界（只 GET 事件端点、无写方法）；
  · verdict 过滤 chips（ALL/PASS/OBSERVE/REJECT）与 commit 搜索框；
  · 近 7 日趋势容器（纯前端由 fetch 数据计算）；
  · HTTP 端点调用串不变（/api/huangzhong/events?limit=50）与 30s 自动刷新；
  · XSS 防护（esc 转义）与 OBSERVE/REJECT 高亮样式。
"""
from __future__ import annotations

import re
from pathlib import Path

_PAGE = Path(__file__).resolve().parents[1] / "static" / "huangzhong_events.html"


def _html() -> str:
    return _PAGE.read_text(encoding="utf-8")


def test_page_exists_and_frontend_trio():
    assert _PAGE.is_file(), "缺少只读页 portal/static/huangzhong_events.html"
    html = _html()
    assert "/static/tokens.css" in html
    assert "/static/nav.js" in html
    assert "viewport" in html


def test_page_readonly_and_fetches_huangzhong_events():
    html = _html()
    # 只读：只 GET 事件端点，无写方法
    assert "/api/huangzhong/events?limit=" in html
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        assert "'%s'" % method not in html
        assert '"%s"' % method not in html
        assert "method:'%s'" % method not in html
        assert 'method:"%s"' % method not in html
    # 只读声明与观察模式口径
    assert "只读" in html
    assert "blocking=false" in html or "观察模式" in html
    # 状态区分
    for verdict in ("PASS", "OBSERVE", "REJECT"):
        assert verdict in html
        assert "bdg." + verdict in html or "<span class=\"bdg " in html


def test_verdict_filter_chips_present():
    html = _html()
    assert 'id="chips"' in html
    assert 'role="group"' in html
    # 四个过滤 chip 按钮（ALL/PASS/OBSERVE/REJECT；CSS 选择器中的 data-verdict 不计）
    for verdict in ("ALL", "PASS", "OBSERVE", "REJECT"):
        assert 'data-verdict="%s"' % verdict in html
    chip_buttons = re.findall(
        r'<button[^>]*data-verdict="(?:ALL|PASS|OBSERVE|REJECT)"', html)
    assert len(chip_buttons) == 4
    # 过滤逻辑在纯前端执行（不新增 API 参数）
    assert "state.verdict" in html
    assert "currentRows" in html


def test_commit_search_box_present():
    html = _html()
    assert 'id="q"' in html
    assert 'type="search"' in html
    assert "搜索 commit" in html
    assert "state.q" in html
    # 搜索基于已 fetch 数据（commit 子串匹配），不触发新请求
    assert "indexOf(q)" in html or "indexOf(state.q" in html or "state.q.toLowerCase" in html


def test_seven_day_trend_card_present():
    html = _html()
    assert "近 7 日趋势" in html
    assert 'id="trend"' in html
    # 趋势由纯前端对 fetch 数据按日聚合（PASS/OBSERVE/REJECT）
    assert "buildTrend" in html
    assert "TREND_DAYS = 7" in html
    for verdict in ("PASS", "OBSERVE", "REJECT"):
        assert verdict in html


def test_api_contract_and_refresh_unchanged():
    html = _html()
    # API 调用串不变：GET /api/huangzhong/events?limit=<N>，limit 仍为 50
    assert "/api/huangzhong/events?limit=" in html
    assert "var LIMIT = 50" in html
    assert "60000" not in html                      # 30s 刷新，未改成其它周期
    assert "30000" in html
    assert "setInterval(load,30000)" in html.replace(" ", "") or \
        "setInterval(load, 30000)" in html
    # 数据源仍是同一个只读端点，无分页/写接口新增
    assert html.count("/api/huangzhong/events") >= 2   # 页面 fetch + footer JSON 链接
    assert "/api/huangzhong/events/" not in html


def test_xss_escape_and_status_styles():
    html = _html()
    assert "function esc(" in html
    assert "&amp;" in html and "&lt;" in html
    # OBSERVE/REJECT 着色（token 变量，不新增自绘色值）
    assert ".bdg.OBSERVE{color:var(--color-warn)}" in html
    assert ".bdg.REJECT{" in html
    assert ".bdg.PASS{color:var(--color-ok)}" in html
