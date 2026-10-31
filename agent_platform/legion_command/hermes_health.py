# -*- coding: utf-8 -*-
"""Hermes 8787 会话感知健康探针（2026-10-31 · 24h 审计项）。

背景：8787 已加登录门（WO-LEGION-V6-P0B）：
  - `GET /` → 302 `/login?next=/`；
  - 无 session 的 API → 401；
  - 有密码文件（`_vault/legion_ide/hermes_webui_password`）时可登录拿 cookie。
旧监控用「200=活」判定：无会话时会把「活着但需登录」误判（或跟随重定向到 /login
而看不到登录门），导致健康卡/治理 KPI 口径与真实状态不一致。

本模块统一判定：
  - 有密码文件 → 用 hermes_auth 带 cookie 探活（真 200 = alive & 非 auth_gated）；
  - 无密码文件 → no-redirect 探测，302→/login 或 401/403 = **alive + auth_gated=True**；
  - 连接失败/5xx → alive=False。
只读；不改变 8787 行为。
"""
from __future__ import annotations

import urllib.error
import urllib.request

try:  # 包内导入
    from .hermes_auth import auth_urlopen as _auth_urlopen
    from .hermes_auth import enabled as _auth_enabled
except ImportError:  # 直接以脚本/单文件方式加载
    from hermes_auth import auth_urlopen as _auth_urlopen  # type: ignore
    from hermes_auth import enabled as _auth_enabled  # type: ignore

DEFAULT_BASE = "http://127.0.0.1:8787"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁止自动跟随：把 302 交回调用方，才能识别 `/login` 登录门。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


def classify(status, location: str = "", error=None) -> dict:
    """把 HTTP 结果归类为 {alive, auth_gated, status, error}（纯函数，可单测）。"""
    loc = location or ""
    if error is not None:
        return {"alive": False, "auth_gated": False, "status": None,
                "error": str(error)[:120]}
    if status is None:
        return {"alive": False, "auth_gated": False, "status": None,
                "error": "no_status"}
    if 200 <= status < 300:
        return {"alive": True, "auth_gated": False, "status": status, "error": ""}
    if status in (301, 302, 303, 307, 308) and "/login" in loc:
        return {"alive": True, "auth_gated": True, "status": status, "error": ""}
    if status in (401, 403):
        return {"alive": True, "auth_gated": True, "status": status, "error": ""}
    return {"alive": False, "auth_gated": False, "status": status,
            "error": "http_%s" % status}


def _raw_get(url: str, timeout: float):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    return opener.open(url, timeout=timeout)


def probe(base: str = DEFAULT_BASE, timeout: float = 2.5, http_get=None) -> dict:
    """探活 8787：有会话用会话，无会话把登录门识别为 auth_gated（而不是 down）。"""
    base = str(base or DEFAULT_BASE).rstrip("/")
    get = http_get or _raw_get
    try:
        if _auth_enabled():
            with _auth_urlopen(base + "/", timeout=timeout) as r:
                return classify(r.status, (r.headers.get("Location") or "") if r.headers else "")
        with get(base + "/", timeout) as r:
            return classify(r.status, (r.headers.get("Location") or "") if r.headers else "")
    except urllib.error.HTTPError as exc:
        try:
            loc = exc.headers.get("Location") if exc.headers else ""
        except Exception:  # noqa: BLE001
            loc = ""
        return classify(exc.code, loc or "")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return classify(None, error=exc)
