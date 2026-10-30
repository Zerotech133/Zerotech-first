# -*- coding: utf-8 -*-
"""页面登录态软保护测试（Phase A，只读）。"""
from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PORTAL = ROOT / "portal"
sys.path.insert(0, str(PORTAL))
import auth_gateway as gw  # noqa: E402


class _H:
    def __init__(self, cookie=""):
        self.headers = {"Cookie": cookie, "Content-Length": "0"}
        self.rfile = io.BytesIO(b"")
        self.wfile = io.BytesIO()
        self.client_address = ("127.0.0.1", 1)
        self.status = None

    def send_response(self, code):
        self.status = code

    def send_header(self, k, v):
        pass

    def end_headers(self):
        pass


def test_guard_requires_session():
    h = _H()
    gw.guard(h)
    assert h.status == 401


def test_guard_accepts_session(monkeypatch):
    sid = gw.STORE.create("zero_bound:bff", {"sub": "zero_bound:bff"}, "t")
    h = _H(cookie="zb_session=" + sid)
    gw.guard(h)
    assert h.status == 200


def test_auth_guard_script_is_readonly():
    js = (PORTAL / "static" / "auth_guard.js").read_text(encoding="utf-8")
    assert "/api/bff/identity/me" in js
    assert "localStorage" not in js
    assert "Authorization" not in js


def test_guard_opted_pages():
    for name in ("os.html", "quant.html", "intel_workbench.html"):
        html = (PORTAL / name).read_text(encoding="utf-8")
        assert "/static/auth_guard.js" in html, name
