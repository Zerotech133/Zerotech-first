# -*- coding: utf-8 -*-
"""P0-07：D1 总览页与 8734 受控入口的静态契约测试（不启动服务）。"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PAGE = REPO / "portal" / "d1" / "index.html"
NGINX = REPO / "portal" / "nginx.conf"


def test_page_is_readonly_and_has_navigation():
    html = PAGE.read_text(encoding="utf-8")
    assert "D1 投研裁决台" in html
    assert 'href="/d1/research"' in html
    assert 'href="/d1/war-room"' in html
    assert 'href="/d1/training"' in html
    assert 'href="/d1/q2/signoff"' in html
    assert "只读聚合" in html
    assert "不产生交易" in html
    assert "paper-only" in html
    assert "不写账本" in html or "不写生产账本" in html
    assert "<form" not in html.lower()


def test_page_consumes_existing_apis_only():
    html = PAGE.read_text(encoding="utf-8")
    for endpoint in (
        '"/api/health"',
        '"/api/d1/research"',
        '"/api/d1/decisions"',
        '"/api/d1/handoffs"',
        '"/api/d1/watch-plans"',
        '"/api/sim/confirmations?status=pending"',
    ):
        assert endpoint in html, endpoint
    assert 'credentials: "same-origin"' in html
    assert "method:" not in html or 'method: "GET"' in html


def test_nginx_exact_d1_route_is_guarded_and_before_prefix():
    conf = NGINX.read_text(encoding="utf-8")
    block = conf.split("location = /d1 {", 1)[1].split("\n    }", 1)[0]
    assert "auth_request /_auth_guard;" in block
    assert "error_page 401 = @login_required;" in block
    assert "try_files /d1/index.html =404;" in block
    assert "X-Content-Type-Options" in block
    assert "location = /d1/ {" in conf
    assert "return 302 /d1;" in conf
    exact = conf.index("location = /d1 {")
    prefix = conf.index("location ^~ /d1/ {")
    assert exact < prefix
