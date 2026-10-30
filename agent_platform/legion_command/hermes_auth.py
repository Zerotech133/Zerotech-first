# -*- coding: utf-8 -*-
"""Hermes WebUI 认证客户端（Phase B1）。

- 仅当密码文件存在时启用（默认路径 _vault/legion_ide/hermes_webui_password）；
- POST /api/auth/login {"password": ...} → 取 hermes_session cookie → 缓存；
- auth_urlopen(url_or_request) 与 safe_urlopen 兼容；对 8787 请求自动带 cookie；
- 401 时清缓存重登一次；密码文件不存在时完全直通（不改变现状）。
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_BASE = "http://127.0.0.1:8787"
DEFAULT_PASSWORD_FILE = r"C:\项目\_vault\legion_ide\hermes_webui_password"
PASSWORD_FILE_ENV = "HERMES_WEBUI_PASSWORD_FILE"
BASE_ENV = "HERMES_BASE"
COOKIE_ENV = "HERMES_WEBUI_COOKIE_NAME"
COOKIE_TTL_S = 12 * 3600

_LOCK = threading.Lock()
_COOKIE = {"value": "", "ts": 0.0}


def _load_safe_urlopen():
    import importlib.util as _ilu
    root = Path(__file__).resolve().parents[2]
    for cand in (root / "agent_platform" / "safe_urlopen.py",
                 root / "portal" / "safe_urlopen.py",
                 root / "tools" / "safe_urlopen.py"):
        if cand.is_file():
            try:
                spec = _ilu.spec_from_file_location("_zb_safe_urlopen", cand)
                mod = _ilu.module_from_spec(spec)
                spec.loader.exec_module(mod)
                return mod.safe_urlopen
            except Exception:
                continue
    return urllib.request.urlopen


safe_urlopen = _load_safe_urlopen()


def base_url() -> str:
    return str(os.environ.get(BASE_ENV) or DEFAULT_BASE).rstrip("/")


def password() -> str:
    p = Path(os.environ.get(PASSWORD_FILE_ENV) or DEFAULT_PASSWORD_FILE)
    try:
        return p.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return ""


def enabled() -> bool:
    return bool(password())


def _cookie_name() -> str:
    return str(os.environ.get(COOKIE_ENV) or "hermes_session").strip() or "hermes_session"


def _login(timeout: float = 5.0) -> str:
    body = json.dumps({"password": password()}).encode("utf-8")
    req = urllib.request.Request(
        base_url() + "/api/auth/login", data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as r:
        raw = r.headers.get("Set-Cookie") or ""
    for part in raw.split(","):
        piece = part.strip().split(";")[0]
        if "=" in piece and piece.split("=", 1)[0].strip() == _cookie_name():
            return piece.strip()
    raise RuntimeError("hermes_login_no_cookie")


def _get_cookie(force: bool = False) -> str:
    with _LOCK:
        now = time.time()
        if (not force and _COOKIE["value"]
                and now - float(_COOKIE["ts"] or 0) < COOKIE_TTL_S):
            return _COOKIE["value"]
        c = _login()
        _COOKIE["value"] = c
        _COOKIE["ts"] = now
        return c


def _target(url_or_req) -> str:
    return str(getattr(url_or_req, "full_url", url_or_req))


def _is_hermes(url: str) -> bool:
    b = base_url()
    return url.startswith(b + "/") or url == b


def auth_urlopen(url_or_req, timeout: float = 30.0):
    """兼容 safe_urlopen(url|Request, timeout=) 的最小子集。"""
    if not enabled():
        return safe_urlopen(url_or_req, timeout=timeout)
    url = _target(url_or_req)
    if not _is_hermes(url):
        return safe_urlopen(url_or_req, timeout=timeout)

    def _with_cookie(cookie: str):
        if isinstance(url_or_req, urllib.request.Request):
            req = url_or_req
            existing = req.get_header("Cookie") or ""
            req.add_header("Cookie", (existing + "; " if existing else "") + cookie)
            return req
        return urllib.request.Request(url_or_req, headers={"Cookie": cookie})

    cookie = _get_cookie()
    try:
        return safe_urlopen(_with_cookie(cookie), timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            cookie = _get_cookie(force=True)
            return safe_urlopen(_with_cookie(cookie), timeout=timeout)
        raise
