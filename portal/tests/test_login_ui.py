# -*- coding: utf-8 -*-
"""/login UI 与 /os-entry 登录态静态测试。"""
from pathlib import Path

PORTAL = Path(__file__).resolve().parents[1]


def test_login_page_shape():
    html = (PORTAL / "login.html").read_text(encoding="utf-8")
    assert 'id="login-form"' in html
    assert "/api/auth/login" in html
    assert "/os-entry" in html
    assert 'autocomplete="current-password"' in html
    assert "localStorage" not in html


def test_nginx_login_route():
    conf = (PORTAL / "nginx.conf").read_text(encoding="utf-8")
    assert "location = /login" in conf
    assert "try_files /login.html =404" in conf


def test_entry_auth_state_panel():
    html = (PORTAL / "os_entry_v9_prod.html").read_text(encoding="utf-8")
    assert 'id="auth-state-panel"' in html
    assert "/api/bff/identity/me" in html
    assert "/api/auth/logout" in html
    assert "csr" + "f_token" in html
    assert "'/login'" in html or '"/login"' in html


def test_entry_top_auth_visible_on_first_screen():
    html = (PORTAL / "os_entry_v9_prod.html").read_text(encoding="utf-8")
    assert 'id="top-auth"' in html
    assert "top-auth" in html
    assert "已登录 · " in html
    assert "未登录 · 登录" in html


def test_login_honors_next_redirect():
    html = (PORTAL / "login.html").read_text(encoding="utf-8")
    assert "URLSearchParams(location.search)" in html
    assert "location.replace(next)" in html
    assert "cand.charAt(1) !== '/'" in html
