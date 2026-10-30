# -*- coding: utf-8 -*-
"""Phase A auth-gateway 单元测试（无网络、无真实凭据）。"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PORTAL = ROOT / "portal"
sys.path.insert(0, str(PORTAL))
import auth_gateway as gw  # noqa: E402


class _H:
    def __init__(self, body=None, cookie="", csrf=""):
        raw = json.dumps(body).encode() if isinstance(body, dict) else (body or b"")
        self.headers = {"Content-Length": str(len(raw)), "Cookie": cookie}
        if csrf:
            self.headers["X-CSRF-Token"] = csrf
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.client_address = ("127.0.0.1", 12345)
        self.status = None
        self.headers_out = []

    def send_response(self, code):
        self.status = code

    def send_header(self, key, value):
        self.headers_out.append((key, value))

    def end_headers(self):
        pass

    @property
    def payload(self):
        self.wfile.seek(0)
        return json.loads(self.wfile.read().decode("utf-8"))

    def cookie(self):
        for k, v in self.headers_out:
            if k.lower() == "set-cookie":
                return v
        return ""


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(gw, "STORE", gw.SessionStore())
    gw._login_fails.clear()


def _login(monkeypatch, username="老田", password="test-pw", user_id="u-96d51f4d9b7a2826"):
    monkeypatch.setattr(gw, "_zhice_login", lambda u, p: {
        "user_id": user_id, "token": "fake-zhice-token", "role": "USER"
    })
    h = _H({"username": username, "password": password})
    gw.login(h)
    return h


def test_login_creates_httponly_session(monkeypatch):
    h = _login(monkeypatch)
    assert h.status == 200
    assert h.payload["ok"] is True
    assert h.payload["sub"] == "zero_bound:owner"
    cookie = h.cookie()
    assert "zb_session=" in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie
    # token 不出现在响应
    assert "fake-zhice-token" not in h.wfile.getvalue().decode("utf-8", "replace")


def test_me_returns_session_identity(monkeypatch):
    h = _login(monkeypatch)
    sid = h.cookie().split("zb_session=")[1].split(";")[0]
    me = _H(cookie="zb_session=" + sid)
    gw.me(me)
    assert me.status == 200
    assert me.payload["session"]["identity_context"]["sub"] == "zero_bound:owner"
    assert me.payload["session"]["identity_context"]["attributes"]["mapping_status"] == "partial"
    assert me.payload["session"]["read_only"] is True
    assert me.payload["csrf_token"]


def test_logout_requires_csrf_and_destroys(monkeypatch):
    h = _login(monkeypatch)
    sid = h.cookie().split("zb_session=")[1].split(";")[0]
    me = _H(cookie="zb_session=" + sid)
    gw.me(me)
    csrf = me.payload["csrf_token"]

    bad = _H(cookie="zb_session=" + sid)
    gw.logout(bad)
    assert bad.status == 403

    good = _H(cookie="zb_session=" + sid, csrf=csrf)
    gw.logout(good)
    assert good.status == 200
    assert good.payload["logged_out"] is True

    after = _H(cookie="zb_session=" + sid)
    gw.me(after)
    assert after.status == 401


def test_login_unmapped_rejected(monkeypatch):
    h = _login(monkeypatch, user_id="u-no-such")
    assert h.status == 403
    assert h.payload["error"] == "identity_not_mapped"
    assert h.cookie() == ""


def test_login_failure_generic(monkeypatch):
    def fail(u, p):
        raise ValueError("invalid_credentials")
    monkeypatch.setattr(gw, "_zhice_login", fail)
    h = _H({"username": "x", "password": "y"})
    gw.login(h)
    assert h.status == 401
    assert h.payload["error"] == "invalid_credentials"


def test_me_without_cookie_401():
    h = _H()
    gw.me(h)
    assert h.status == 401


def test_session_store_expiry():
    store = gw.SessionStore(ttl=1)
    sid = store.create("sub", {"sub": "sub"}, "t")
    assert store.get(sid) is not None
    store.delete(sid)
    assert store.get(sid) is None


def test_portal_routes_are_wired():
    src = (PORTAL / "portal_api.py").read_text(encoding="utf-8")
    assert "auth_gateway.handle_get" in src
    assert "auth_gateway.handle_post" in src
    assert "'/api/bff/identity/me'" in src
