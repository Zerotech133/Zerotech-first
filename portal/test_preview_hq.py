# -*- coding: utf-8 -*-
"""本机只读预览：页面与 nginx 边界静态断言。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "portal" / "preview_hq.html"
NGINX = ROOT / "portal" / "nginx.conf"


def test_preview_page_exists_and_has_no_auth_wall():
    s = PAGE.read_text(encoding="utf-8")
    assert "本机只读预览" in s
    assert "/preview/hq" in s
    assert "/api/preview/hq/" in s
    for name in ("roles", "agenda", "directives", "reports"):
        assert "get('" + name + "')" in s


def test_preview_page_does_not_fetch_sensitive_apis():
    s = PAGE.read_text(encoding="utf-8")
    for forbidden in ("/api/hq/snapshot", "/api/hq/approvals", "/api/hq/commercial",
                      "/api/hq/budget", "/api/preview/hq/snapshot"):
        assert forbidden not in s


def test_preview_page_has_no_write_ui():
    s = PAGE.read_text(encoding="utf-8")
    assert "<form" not in s
    assert "method=\"post\"" not in s.lower()
    assert "fetch(BASE" in s


def test_nginx_preview_page_and_api_are_whitelisted():
    s = NGINX.read_text(encoding="utf-8")
    assert "location = /preview/hq" in s
    assert "try_files /preview_hq.html =404;" in s
    assert r"location ~ ^/api/preview/hq/(roles|agenda|directives|reports)$" in s
    assert "limit_except GET" in s


def test_nginx_formal_hq_keeps_auth():
    s = NGINX.read_text(encoding="utf-8")
    # /hq 与 /api/hq/ 仍有 auth_request（不对 preview 块做全局替换断言）
    assert "location = /hq {" in s
    assert "location ^~ /api/hq/ {" in s
    assert "auth_request /_auth_guard;" in s
