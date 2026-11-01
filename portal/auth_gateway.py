# -*- coding: utf-8 -*-
"""Phase A auth-gateway（2026-10-30，单独模块，与只读 BFF 分离）。

职责：
- POST /api/auth/login   用智策 /auth/login 校验，创建服务端 session，Set-Cookie；
- POST /api/auth/logout  销毁 session，清 cookie；
- GET  /api/auth/me      返回当前 SessionContext（alias）；
- GET  /api/bff/identity/me  返回当前 SessionContext + IdentityContext。

安全边界：
- 密码只在本次请求内存中出现；不落盘、不写日志；
- 智策 token 只存服务端 session，不下发浏览器；
- cookie HttpOnly + SameSite=Lax；本地 HTTP 不加 Secure（HTTPS 部署时加）；
- 只允许 identity_registry 中已验证的智策账号登录；
- session 内存存储，进程重启即失效（Phase A 明确取舍）；
- 不鉴权、不写交易/业务数据。

Phase A 之后由真正的 OIDC/token exchange 替换本模块。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
try:
    from safe_urlopen import safe_urlopen
except ImportError:
    from portal.safe_urlopen import safe_urlopen

BEIJING = timezone(timedelta(hours=8))
COOKIE_NAME = "zb_session"
SESSION_TTL_SECONDS = 3600
MAX_BODY = 64 * 1024
MAX_LOGIN_FAILS = 5
LOGIN_WINDOW_SECONDS = 300
ZHICE_BASE = os.environ.get("ZHICE_BASE_URL", "http://127.0.0.1:8105").rstrip("/")
# 本地管理员（2026-10-30 老田要求）：凭据只存 vault 的 PBKDF2 哈希，不入库。
ADMIN_FILE = os.environ.get("ZB_LOCAL_ADMIN_FILE", r"C:\项目\_vault\secrets\zb_admin.env")
ADMIN_PBKDF2_ITER = 260000

_sessions: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()
_login_fails: Dict[str, list] = {}


def _now() -> datetime:
    return datetime.now(BEIJING)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _respond(handler, obj, code=200, set_cookie=None):
    """认证面独立响应器：先 set-cookie 再 body（handler._json 无法带 cookie）。"""
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    if set_cookie:
        handler.send_header("Set-Cookie", set_cookie)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _json_safe(handler, obj, code=200, set_cookie=None):
    _respond(handler, obj, code, set_cookie=set_cookie)


class SessionStore:
    """线程安全内存 session store（Phase A）。"""

    def __init__(self, ttl: int = SESSION_TTL_SECONDS):
        self.ttl = int(ttl)
        self._data: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def create(self, sub: str, identity_context: Dict[str, Any], zhice_token: str,
               idp: str = "zhice", amr: list | None = None) -> str:
        sid = secrets.token_urlsafe(32)
        now = _now()
        csrf = secrets.token_urlsafe(24)
        rec = {
            "session_id": sid,
            "sub": sub,
            "capacity": "readonly",
            "identity_context": identity_context,
            "csrf_hash": hashlib.sha256(csrf.encode("utf-8")).hexdigest(),
            "issued_at": _iso(now),
            "auth_time": _iso(now),
            "expires_at": _iso(now + timedelta(seconds=self.ttl)),
            "_csrf": csrf,
            "zhice_token": zhice_token,
            "idp": idp,
            "amr": list(amr or ["pwd"]),
        }
        with self._lock:
            self._purge_locked()
            self._data[sid] = rec
        return sid

    def get(self, sid: str) -> Optional[Dict[str, Any]]:
        if not sid:
            return None
        with self._lock:
            self._purge_locked()
            rec = self._data.get(sid)
            return dict(rec) if rec else None

    def delete(self, sid: str) -> bool:
        with self._lock:
            return self._data.pop(sid, None) is not None

    def _purge_locked(self) -> None:
        now = _now()
        expired = []
        for sid, rec in self._data.items():
            try:
                if datetime.fromisoformat(rec["expires_at"]) <= now:
                    expired.append(sid)
            except (KeyError, ValueError):
                expired.append(sid)
        for sid in expired:
            self._data.pop(sid, None)


STORE = SessionStore()


def _client_ip(handler) -> str:
    real = (handler.headers.get("X-Real-IP") or "").strip() if getattr(handler, "headers", None) else ""
    if real:
        return real
    addr = getattr(handler, "client_address", None)
    return addr[0] if addr else "?"


def _cookie_header(handler) -> str:
    return (handler.headers.get("Cookie") or "") if getattr(handler, "headers", None) else ""


def _cookie_value(handler) -> str:
    for part in _cookie_header(handler).split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE_NAME:
            return v.strip()
    return ""


def _cookie_header_value(sid: str, max_age: int = SESSION_TTL_SECONDS) -> str:
    return "%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d" % (COOKIE_NAME, sid, max_age)


def _http_json(url: str, payload: Optional[dict] = None, token: str = "", timeout: float = 6.0) -> Tuple[int, Any]:
    headers = {"Accept": "application/json"}
    data = None
    method = "GET"
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, headers=headers, data=data, method=method)
    try:
        with safe_urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return 0, None


def _zhice_login(username: str, password: str) -> Dict[str, Any]:
    """返回 {user_id, token, role}；失败抛 ValueError(不含敏感信息)。"""
    status, data = _http_json(ZHICE_BASE + "/api/v1/auth/login",
                              {"username": username, "password": password})
    if status != 200 or not isinstance(data, dict) or not data.get("token"):
        raise ValueError("invalid_credentials")
    token = str(data["token"])
    status, me = _http_json(ZHICE_BASE + "/api/v1/auth/me", token=token)
    if status != 200 or not isinstance(me, dict) or not me.get("user_id"):
        raise ValueError("identity_verify_failed")
    return {"user_id": str(me["user_id"]), "token": token, "role": str(me.get("role") or "")}


def _admin_cfg() -> Dict[str, str]:
    """本地管理员配置：环境变量优先，其次 vault 文件；只存哈希。"""
    cfg: Dict[str, str] = {}
    try:
        with open(ADMIN_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    except OSError:
        pass
    for key in ("ZB_ADMIN_USERNAME", "ZB_ADMIN_SALT", "ZB_ADMIN_HASH"):
        if os.environ.get(key):
            cfg[key] = str(os.environ[key])
    if not all(cfg.get(k) for k in ("ZB_ADMIN_USERNAME", "ZB_ADMIN_SALT", "ZB_ADMIN_HASH")):
        return {}
    return cfg


def _local_admin_password_ok(username: str, password: str) -> bool:
    cfg = _admin_cfg()
    if not cfg or not hmac.compare_digest(
            str(username or "").encode("utf-8"),
            cfg["ZB_ADMIN_USERNAME"].encode("utf-8")):
        return False
    try:
        salt = bytes.fromhex(cfg["ZB_ADMIN_SALT"])
        calc = hashlib.pbkdf2_hmac(
            "sha256", str(password or "").encode("utf-8"), salt,
            ADMIN_PBKDF2_ITER).hex()
    except ValueError:
        return False
    return hmac.compare_digest(calc, cfg["ZB_ADMIN_HASH"].lower())


def _local_admin_ctx() -> Optional[Dict[str, Any]]:
    try:
        import identity_registry as reg
        return reg.resolve("zero_bound:owner")
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _rate_limited(ip: str, username: str) -> bool:
    key = "%s:%s" % (ip, username.lower())
    now = time.time()
    with _lock:
        arr = [t for t in _login_fails.get(key, []) if now - t < LOGIN_WINDOW_SECONDS]
        if arr:
            _login_fails[key] = arr
        return len(arr) >= MAX_LOGIN_FAILS


def _record_fail(ip: str, username: str) -> None:
    key = "%s:%s" % (ip, username.lower())
    with _lock:
        if len(_login_fails) > 5000:
            # 有界：清过期 key，仍超限则丢最旧 key
            stale = [k for k, arr in _login_fails.items()
                     if not any(time.time() - t < LOGIN_WINDOW_SECONDS for t in arr)]
            for k in stale:
                _login_fails.pop(k, None)
            while len(_login_fails) > 5000:
                _login_fails.pop(next(iter(_login_fails)), None)
        _login_fails.setdefault(key, []).append(time.time())


def _clear_fails(ip: str, username: str) -> None:
    with _lock:
        _login_fails.pop("%s:%s" % (ip, username.lower()), None)


def _read_body(handler) -> Tuple[bool, bytes]:
    try:
        ln = int(handler.headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        return False, b""
    if ln < 0 or ln > MAX_BODY:
        return False, b""
    return True, (handler.rfile.read(ln) if ln else b"")


def _session_payload(rec: Dict[str, Any]) -> Dict[str, Any]:
    ctx = rec["identity_context"]
    session = {
        "session_id": rec["session_id"],
        "sub": rec["sub"],
        "idp": rec.get("idp", "zhice"),
        "auth_time": rec["auth_time"],
        "issued_at": rec["issued_at"],
        "expires_at": rec["expires_at"],
        "amr": rec.get("amr", ["pwd"]),
        "read_only": True,
        "identity_context": ctx,
        "csrf_token_hash": rec["csrf_hash"],
        "source_ids": ctx.get("source_ids", {}),
    }
    return {"ok": True, "mode": "session", "session": session, "csrf_token": rec["_csrf"]}


def login(handler) -> None:
    ok, raw = _read_body(handler)
    if not ok:
        _json_safe(handler, {"ok": False, "error": "bad_body"}, 400)
        return
    try:
        body = json.loads(raw.decode("utf-8", "replace")) if raw else {}
    except ValueError:
        _json_safe(handler, {"ok": False, "error": "bad_json"}, 400)
        return
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    if len(username) > 128 or len(password) > 4096:
        _json_safe(handler, {"ok": False, "error": "invalid_credentials"}, 400)
        return
    if not username or not password:
        _json_safe(handler, {"ok": False, "error": "missing_credentials"}, 400)
        return
    ip = _client_ip(handler)
    if _rate_limited(ip, username):
        _json_safe(handler, {"ok": False, "error": "too_many_attempts"}, 429)
        return
    admin_cfg = _admin_cfg()
    if admin_cfg and hmac.compare_digest(
            username.encode("utf-8"),
            admin_cfg["ZB_ADMIN_USERNAME"].encode("utf-8")):
        if not _local_admin_password_ok(username, password):
            _record_fail(ip, username)
            _json_safe(handler, {"ok": False, "error": "invalid_credentials"}, 401)
            return
        ctx = _local_admin_ctx()
        if ctx is None:
            _json_safe(handler, {"ok": False, "error": "identity_registry_unavailable"}, 503)
            return
        _clear_fails(ip, username)
        sid = STORE.create(ctx["sub"], ctx, "", idp="local-admin")
        _json_safe(handler, {"ok": True, "sub": ctx["sub"], "local_admin": True,
                             "expires_in": SESSION_TTL_SECONDS},
                   set_cookie=_cookie_header_value(sid))
        return
    try:
        ident = _zhice_login(username, password)
    except ValueError:
        _record_fail(ip, username)
        _json_safe(handler, {"ok": False, "error": "invalid_credentials"}, 401)
        return
    try:
        import identity_registry as reg
        ctx = reg.resolve_source("zhice", ident["user_id"])
    except (OSError, ValueError, TypeError, KeyError):
        _json_safe(handler, {"ok": False, "error": "identity_registry_unavailable"}, 503)
        return
    if ctx is None:
        _record_fail(ip, username)
        _json_safe(handler, {"ok": False, "error": "identity_not_mapped"}, 403)
        return
    _clear_fails(ip, username)
    sid = STORE.create(ctx["sub"], ctx, ident["token"])
    _json_safe(handler, {"ok": True, "sub": ctx["sub"], "expires_in": SESSION_TTL_SECONDS},
               set_cookie=_cookie_header_value(sid))
    _ = password  # noqa: F841 - 仅本次调用使用，不落存储


def logout(handler) -> None:
    sid = _cookie_value(handler)
    rec = STORE.get(sid) if sid else None
    if rec:
        token = (handler.headers.get("X-CSRF-Token") or "").strip()
        if not token or not hmac.compare_digest(
                hashlib.sha256(token.encode("utf-8")).hexdigest(), rec["csrf_hash"]):
            _json_safe(handler, {"ok": False, "error": "csrf_failed"}, 403)
            return
        STORE.delete(sid)
    _json_safe(handler, {"ok": True, "logged_out": True},
               set_cookie=_cookie_header_value("", max_age=0))


def me(handler) -> None:
    sid = _cookie_value(handler)
    rec = STORE.get(sid) if sid else None
    if not rec:
        _json_safe(handler, {"ok": False, "error": "unauthorized"}, 401)
        return
    _json_safe(handler, _session_payload(rec))


def guard(handler) -> None:
    """供 nginx auth_request 使用：有效 session → 200；否则 401。只读。"""
    rec = STORE.get(_cookie_value(handler))
    if not rec:
        _json_safe(handler, {"ok": False, "error": "unauthorized"}, 401)
        return
    _json_safe(handler, {"ok": True, "sub": rec["sub"], "read_only": True})


def status(handler) -> None:
    rec = STORE.get(_cookie_value(handler))
    _json_safe(handler, {"ok": True, "authenticated": bool(rec), "sub": rec["sub"] if rec else None})


def handle_get(handler, path: str) -> None:
    if path.startswith("/api/auth/oidc/"):
        import auth_oidc
        try:
            import auth_oidc_runtime
            auth_oidc_runtime.apply()
        except Exception:
            pass
        auth_oidc.handle_get(handler, path)
        return
    if path in ("/api/auth/me", "/api/bff/identity/me"):
        me(handler)
        return
    if path == "/api/auth/status":
        status(handler)
        return
    if path == "/api/auth/guard":
        guard(handler)
        return
    _json_safe(handler, {"ok": False, "error": "not_found"}, 404)


def handle_post(handler, path: str) -> None:
    if path == "/api/auth/login":
        login(handler)
        return
    if path == "/api/auth/logout":
        logout(handler)
        return
    _json_safe(handler, {"ok": False, "error": "not_found"}, 404)
