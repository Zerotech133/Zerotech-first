# -*- coding: utf-8 -*-
"""nginx auth_request 页面真保护配置回归（2026-10-30 L2）。"""
from __future__ import annotations

import re
from pathlib import Path

CONF = Path(__file__).resolve().parents[1] / "nginx.conf"
PROTECTED = ("/os-entry", "/os-board", "/devops", "/model_admin", "/approvals")


def _text():
    return CONF.read_text(encoding="utf-8")


def test_no_bom():
    raw = CONF.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "nginx.conf 不得带 UTF-8 BOM（nginx -t 会失败）"


def test_auth_guard_internal_block():
    s = _text()
    assert "location = /_auth_guard" in s
    assert "internal;" in s
    assert "proxy_pass http://$h:8091/api/auth/guard" in s
    assert "proxy_set_header Cookie $http_cookie" in s


def test_relative_redirect():
    s = _text()
    assert "location @login_required" in s
    assert "absolute_redirect off;" in s
    assert "return 302 /login?next=$uri;" in s


def test_five_pages_protected():
    s = _text()
    # 2026-10-30 P0：IDE 两个入口也纳入统一认证（五页 + /ide + /hermes/）
    assert s.count("auth_request /_auth_guard;") == len(PROTECTED) + 2
    for p in PROTECTED + ("/ide",):
        m = re.search(r"location = " + re.escape(p) + r" \{(.*?)\n    \}", s, re.S)
        assert m, p
        assert "auth_request /_auth_guard;" in m.group(1), p
        assert "error_page 401 = @login_required;" in m.group(1), p
    m = re.search(r"location \^~ /hermes/ \{(.*?)\n    \}", s, re.S)
    assert m and "auth_request /_auth_guard;" in m.group(1)
    assert "error_page 401 = @login_required;" in m.group(1)


def test_login_not_protected():
    s = _text()
    m = re.search(r"location = /login[^\n]*", s)
    assert m
    assert "auth_request" not in m.group(0)


def test_other_pages_not_locked():
    s = _text()
    for p in ("/quant", "/intel", "/api/"):
        assert ("location = " + p) not in s or "auth_request" not in s.split("location = " + p, 1)[1][:120]
