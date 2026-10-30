# -*- coding: utf-8 -*-
"""B1 OIDC 客户端（Pocket ID，默认关；不替换密码登录入口）。

端点（均由 auth_gateway 转发）：
  GET /api/auth/oidc/status   → mode / ready（只读自述）
  GET /api/auth/oidc/start    → 生成 state/nonce/PKCE，302 到 Pocket ID
  GET /api/auth/oidc/callback → 校验 state/nonce/code，换 token，验 id_token，建 session

安全红线：
- 默认 `AUTH_OIDC_MODE=off`，off 时 start/callback 一律 404；
- id_token 必须校验签名/iss/aud/exp/nonce；
- PKCE S256；state 单次使用；
- access_token/id_token 只服务端使用，不下发浏览器；
- 未映射 idp_sub → 403，不自动建身份；
- 不改变 Phase A 密码桥与只读 BFF。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import auth_gateway as gw
try:
    from safe_urlopen import safe_urlopen
except ImportError:
    from portal.safe_urlopen import safe_urlopen

MODE = (os.environ.get("AUTH_OIDC_MODE") or "off").strip().lower()
ISSUER = (os.environ.get("OIDC_ISSUER") or "http://localhost:1411").rstrip("/")
CLIENT_ID = (os.environ.get("OIDC_CLIENT_ID") or "").strip()
CLIENT_SECRET = (os.environ.get("OIDC_CLIENT_SECRET") or "").strip()
REDIRECT_URI = (os.environ.get("OIDC_REDIRECT_URI")
                or "http://localhost:8734/api/auth/oidc/callback").strip()
SCOPES = (os.environ.get("OIDC_SCOPES") or "openid profile email").strip()
CRED_FILE = Path(os.environ.get(
    "OIDC_CREDENTIALS_FILE",
    r"C:\aios\_vault\pocket-id\secrets\zero_bound_client.env",
))
STATE_TTL = 600
_states: Dict[str, Dict[str, Any]] = {}
_discovery_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_jwks_cache: Dict[str, Tuple[float, list]] = {}


def _now() -> float:
    return time.time()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _safe_next(value: str) -> str:
    """只允许同源绝对路径；拒绝 //、反斜杠、控制字符、scheme/netloc。"""
    value = (value or "").strip()
    if not value.startswith("/") or value.startswith("//"):
        return "/os-entry"
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F or ch == "\\" for ch in value):
        return "/os-entry"
    try:
        parts = urllib.parse.urlsplit(value)
    except ValueError:
        return "/os-entry"
    if parts.scheme or parts.netloc or parts.path.startswith("//"):
        return "/os-entry"
    return value


def _load_credentials() -> None:
    global CLIENT_ID, CLIENT_SECRET
    if CLIENT_ID and CLIENT_SECRET:
        return
    try:
        vals = {}
        for line in CRED_FILE.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip()
        CLIENT_ID = CLIENT_ID or vals.get("OIDC_CLIENT_ID", "")
        CLIENT_SECRET = CLIENT_SECRET or vals.get("OIDC_CLIENT_SECRET", "")
    except OSError:
        pass


def _ready() -> bool:
    _load_credentials()
    return bool(ISSUER and CLIENT_ID and CLIENT_SECRET and REDIRECT_URI)


def _http_json(url: str, timeout: float = 8.0) -> Tuple[int, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with safe_urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return 0, None


def _discovery() -> Optional[Dict[str, Any]]:
    now = _now()
    cached = _discovery_cache.get(ISSUER)
    if cached and now - cached[0] < 600:
        return cached[1]
    status, data = _http_json(ISSUER + "/.well-known/openid-configuration")
    if status != 200 or not isinstance(data, dict):
        return None
    _discovery_cache[ISSUER] = (now, data)
    return data


def _post_form(url: str, data: Dict[str, str]) -> Tuple[int, Any]:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    try:
        with safe_urlopen(req, timeout=12) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return 0, None


def _redirect(handler, url: str, set_cookie: str = "") -> None:
    if not isinstance(url, str) or any(ch in url for ch in ("\r", "\n")):
        url = "/os-entry"
    handler.send_response(302)
    handler.send_header("Location", url)
    if set_cookie:
        handler.send_header("Set-Cookie", set_cookie)
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", "0")
    handler.end_headers()


def _error(handler, code: int, reason: str) -> None:
    gw._json_safe(handler, {"ok": False, "error": reason, "mode": "oidc"}, code)


def status() -> Dict[str, Any]:
    return {
        "ok": True,
        "mode": MODE,
        "issuer": ISSUER,
        "redirect_uri": REDIRECT_URI,
        "ready": _ready(),
        "login_entry": "unchanged (Phase A password bridge)" if MODE == "off" else "oidc_available",
    }


def start(handler, query: str = "") -> None:
    if MODE == "off":
        _error(handler, 404, "oidc_disabled")
        return
    if not _ready():
        _error(handler, 503, "oidc_not_configured")
        return
    disc = _discovery()
    if not disc or not disc.get("authorization_endpoint"):
        _error(handler, 503, "oidc_discovery_unavailable")
        return
    q = urllib.parse.parse_qs(query)
    next_path = _safe_next((q.get("next") or ["/os-entry"])[0])
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    now = _now()
    for k in [k for k, v in _states.items() if now - v["created"] > STATE_TTL]:
        _states.pop(k, None)
    _states[state] = {
        "nonce": nonce, "verifier": verifier, "next": next_path,
        "created": now, "issuer": ISSUER,
    }
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    _redirect(handler, disc["authorization_endpoint"] + "?" + urllib.parse.urlencode(params))


def _get_jwks(disc: Dict[str, Any]) -> list:
    uri = disc.get("jwks_uri") or ""
    now = _now()
    cached = _jwks_cache.get(uri)
    if cached and now - cached[0] < 600:
        return cached[1]
    status, data = _http_json(uri)
    if status != 200 or not isinstance(data, dict) or not isinstance(data.get("keys"), list):
        return []
    _jwks_cache[uri] = (now, data["keys"])
    return data["keys"]


def _verify_id_token(id_token: str, disc: Dict[str, Any], expected_nonce: str) -> Dict[str, Any]:
    import jwt  # PyJWT
    keys = _get_jwks(disc)
    if not keys:
        raise ValueError("oidc_jwks_unavailable")
    header = jwt.get_unverified_header(id_token)
    kid = header.get("kid")
    key = None
    for jwk in keys:
        if jwk.get("kty") == "RSA" and (not kid or jwk.get("kid") == kid):
            key = jwt.PyJWK(jwk).key
            break
    if key is None:
        raise ValueError("oidc_key_not_found")
    claims = jwt.decode(
        id_token, key, algorithms=["RS256"],
        audience=CLIENT_ID, issuer=ISSUER,
        options={"require": ["exp", "iat", "sub", "aud", "iss"]},
    )
    if not secrets.compare_digest(str(claims.get("nonce", "")), str(expected_nonce)):
        raise ValueError("oidc_nonce_mismatch")
    return claims


def callback(handler, query: str = "") -> None:
    if MODE == "off":
        _error(handler, 404, "oidc_disabled")
        return
    q = urllib.parse.parse_qs(query or "")
    state = (q.get("state") or [""])[0]
    code = (q.get("code") or [""])[0]
    if not state or not code:
        _error(handler, 400, "oidc_missing_code_or_state")
        return
    rec = _states.pop(state, None)
    if not rec:
        _error(handler, 400, "oidc_state_invalid")
        return
    if _now() - rec["created"] > STATE_TTL:
        _error(handler, 400, "oidc_state_expired")
        return
    disc = _discovery()
    if not disc or not disc.get("token_endpoint"):
        _error(handler, 503, "oidc_discovery_unavailable")
        return
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code_verifier": rec["verifier"],
    }
    status, tok = _post_form(disc["token_endpoint"], form)
    if status != 200 or not isinstance(tok, dict) or not tok.get("id_token"):
        _error(handler, 401, "oidc_token_exchange_failed")
        return
    try:
        claims = _verify_id_token(str(tok["id_token"]), disc, rec["nonce"])
    except Exception as exc:  # noqa: BLE001
        _error(handler, 401, "oidc_id_token_invalid:%s" % type(exc).__name__)
        return
    sub = str(claims.get("sub") or "")
    if not sub:
        _error(handler, 401, "oidc_sub_missing")
        return
    try:
        import identity_registry as reg
        ctx = reg.resolve_oidc(ISSUER, sub)
    except Exception:
        ctx = None
    if ctx is None:
        _error(handler, 403, "identity_not_mapped")
        return
    amr = claims.get("amr") if isinstance(claims.get("amr"), list) else ["passkey", "sso"]
    sid = gw.STORE.create(ctx["sub"], ctx, str(tok.get("access_token") or ""),
                          idp="pocket_id", amr=amr)
    _redirect(handler, rec.get("next") or "/os-entry",
              set_cookie=gw._cookie_header_value(sid))


def handle_get(handler, path: str) -> None:
    qs = handler.path.split("?", 1)[1] if "?" in handler.path else ""
    if path == "/api/auth/oidc/status":
        gw._json_safe(handler, status())
        return
    if path == "/api/auth/oidc/start":
        start(handler, qs)
        return
    if path == "/api/auth/oidc/callback":
        callback(handler, qs)
        return
    _error(handler, 404, "oidc_not_found")
