# -*- coding: utf-8 -*-
"""B1 OIDC 客户端测试：默认关、start/PKCE、callback/JWT/nonce、session/映射。"""
from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

ROOT = Path(__file__).resolve().parents[2]
PORTAL = ROOT / "portal"
sys.path.insert(0, str(PORTAL))
import auth_gateway as gw  # noqa: E402
import auth_oidc  # noqa: E402
import identity_registry  # noqa: E402


class _H:
    def __init__(self, path="/api/auth/oidc/start"):
        self.path = path
        self.headers = {}
        self.client_address = ("127.0.0.1", 1)
        self.rfile = io.BytesIO(b"")
        self.wfile = io.BytesIO()
        self.status = None
        self.out = []

    def send_response(self, code):
        self.status = code

    def send_header(self, k, v):
        self.out.append((k.lower(), v))

    def end_headers(self):
        pass

    def location(self):
        for k, v in self.out:
            if k == "location":
                return v
        return ""

    def cookie(self):
        for k, v in self.out:
            if k == "set-cookie":
                return v
        return ""


@pytest.fixture()
def subject(monkeypatch):
    monkeypatch.setattr(gw, "STORE", gw.SessionStore())
    monkeypatch.setattr(auth_oidc, "MODE", "on")
    monkeypatch.setattr(auth_oidc, "ISSUER", "https://idp.test")
    monkeypatch.setattr(auth_oidc, "CLIENT_ID", "zero-bound-bff")
    monkeypatch.setattr(auth_oidc, "CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(auth_oidc, "REDIRECT_URI", "http://localhost:8734/api/auth/oidc/callback")
    auth_oidc._states.clear()
    auth_oidc._discovery_cache.clear()
    auth_oidc._jwks_cache.clear()
    return monkeypatch


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk(private):
    jwk = json.loads(RSAAlgorithm.to_jwk(private.public_key()))
    jwk.update({"kid": "k1", "use": "sig", "alg": "RS256"})
    return jwk


def _disc():
    return {
        "authorization_endpoint": "https://idp.test/authorize",
        "token_endpoint": "https://idp.test/token",
        "jwks_uri": "https://idp.test/jwks",
    }


def test_off_by_default(monkeypatch):
    monkeypatch.setattr(auth_oidc, "MODE", "off")
    h = _H()
    auth_oidc.start(h)
    assert h.status == 404
    assert h.wfile.getvalue().decode().find("oidc_disabled") >= 0


def test_status_reports_mode(subject):
    h = _H()
    auth_oidc.handle_get(h, "/api/auth/oidc/status")
    body = json.loads(h.wfile.getvalue())
    assert body["mode"] == "on"
    assert body["login_entry"] == "oidc_available"


def test_start_pkce_state(subject):
    subject.setattr(auth_oidc, "_discovery", lambda: _disc())
    h = _H()
    auth_oidc.start(h, "next=/os-board")
    assert h.status == 302
    loc = h.location()
    assert "code_challenge=" in loc and "code_challenge_method=S256" in loc
    assert "state=" in loc and "nonce=" in loc
    assert auth_oidc._states
    assert _binding_cookie(h)


def _binding_cookie(h):
    for k, v in h.out:
        if k == "set-cookie" and v.startswith("zb_oidc_state="):
            return v.split(";")[0]
    return ""


def _issue(private, nonce, sub="u-oidc"):
    now = int(time.time())
    return jwt.encode(
        {"iss": "https://idp.test", "aud": "zero-bound-bff", "sub": sub,
         "exp": now + 300, "iat": now, "nonce": nonce, "amr": ["passkey"]},
        private, algorithm="RS256", headers={"kid": "k1"},
    )


def test_callback_creates_session(subject):
    private = _key()
    jwk = _jwk(private)
    h = _H()
    subject.setattr(auth_oidc, "_discovery", lambda: _disc())
    auth_oidc.start(h, "next=/os-board")
    state = [k for k in auth_oidc._states][0]
    nonce = auth_oidc._states[state]["nonce"]
    token = _issue(private, nonce)

    subject.setattr(auth_oidc, "_get_jwks", lambda disc: [jwk])
    subject.setattr(auth_oidc, "_post_form", lambda url, form: (200, {"id_token": token, "access_token": "at"}))
    ctx = {"sub": "zero_bound:owner", "actor_type": "member", "source": "zero_bound",
           "roles": ["ADMIN"], "permissions": ["view"], "owner_scope": "global",
           "issued_at": "2026-10-30T00:00:00+08:00", "attributes": {"mapping_status": "partial"}}
    subject.setattr(identity_registry, "resolve_oidc", lambda issuer, sub: ctx)

    cb = _H("/api/auth/oidc/callback?code=abc&state=" + state)
    cb.headers["Cookie"] = _binding_cookie(h)
    auth_oidc.callback(cb, "code=abc&state=" + state)
    assert cb.status == 302
    assert cb.location() == "/os-board"
    cookie = cb.cookie()
    assert "zb_session=" in cookie and "HttpOnly" in cookie
    sid = cookie.split("zb_session=")[1].split(";")[0]
    rec = gw.STORE.get(sid)
    assert rec["idp"] == "pocket_id"
    assert rec["amr"] == ["passkey"]
    assert rec["sub"] == "zero_bound:owner"


def test_callback_rejects_bad_nonce(subject):
    private = _key()
    jwk = _jwk(private)
    h = _H()
    subject.setattr(auth_oidc, "_discovery", lambda: _disc())
    auth_oidc.start(h)
    state = [k for k in auth_oidc._states][0]
    token = _issue(private, "wrong-nonce")
    subject.setattr(auth_oidc, "_get_jwks", lambda disc: [jwk])
    subject.setattr(auth_oidc, "_post_form", lambda url, form: (200, {"id_token": token}))
    cb = _H("/api/auth/oidc/callback?code=abc&state=" + state)
    cb.headers["Cookie"] = _binding_cookie(h)
    auth_oidc.callback(cb, "code=abc&state=" + state)
    assert cb.status == 401
    assert "nonce" in cb.wfile.getvalue().decode() or "invalid" in cb.wfile.getvalue().decode()


def test_callback_unmapped(subject):
    private = _key()
    jwk = _jwk(private)
    h = _H()
    subject.setattr(auth_oidc, "_discovery", lambda: _disc())
    auth_oidc.start(h)
    state = [k for k in auth_oidc._states][0]
    token = _issue(private, auth_oidc._states[state]["nonce"])
    subject.setattr(auth_oidc, "_get_jwks", lambda disc: [jwk])
    subject.setattr(auth_oidc, "_post_form", lambda url, form: (200, {"id_token": token}))
    subject.setattr(identity_registry, "resolve_oidc", lambda issuer, sub: None)
    cb = _H("/api/auth/oidc/callback?code=abc&state=" + state)
    cb.headers["Cookie"] = _binding_cookie(h)
    auth_oidc.callback(cb, "code=abc&state=" + state)
    assert cb.status == 403
    assert b"identity_not_mapped" in cb.wfile.getvalue()


def test_callback_requires_state_binding_cookie(subject):
    h = _H()
    subject.setattr(auth_oidc, "_discovery", lambda: _disc())
    auth_oidc.start(h)
    state = next(iter(auth_oidc._states))
    cb = _H("/api/auth/oidc/callback?code=abc&state=" + state)
    auth_oidc.callback(cb, "code=abc&state=" + state)
    assert cb.status == 400
    assert b"oidc_state_binding_invalid" in cb.wfile.getvalue()


def test_safe_next_rejects_open_redirect_and_headers():
    assert auth_oidc._safe_next("/os-board") == "/os-board"
    assert auth_oidc._safe_next("/os-board?x=1") == "/os-board?x=1"
    for bad in ("//evil.example", "/\\evil.example", "/\r\nSet-Cookie: x=1",
                "http://evil.example/", "/\t/evil.example"):
        assert auth_oidc._safe_next(bad) == "/os-entry", bad
