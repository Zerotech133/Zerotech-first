# -*- coding: utf-8 -*-
"""移动端页面令牌（HMAC-SHA256）v1（2026-10-25）。

用途：
  · 飞书卡片按钮短链：/m/<scene>?t=<page_token>
  · 外部分享：/s/<share_token>
令牌载荷统一为 JSON（含 scene/id/code/scope/exp/nbf/chat），
base64url(payload) + "." + base64url(signature)。

安全：
  · 密钥存 portal/work/m_link_secret.txt（32 字节 hex，首次自动生成，gitignored）
  · 校验 exp/nbf/scope；错误一律抛 TokenError（调用方返回 401/403，不落日志明文）
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SECRET_FILE = os.environ.get("M_LINK_SECRET_FILE") or os.path.join(
    HERE, "work", "m_link_secret.txt"
)
DEFAULT_TTL = 7 * 86400


class TokenError(ValueError):
    """令牌非法/过期/作用域不匹配。"""


def _b64e(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(s):
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def load_secret(path=None):
    path = path or SECRET_FILE
    try:
        with open(path, encoding="ascii") as f:
            sec = f.read().strip()
        if len(sec) >= 32:
            return sec.encode("ascii")
    except OSError:
        pass
    sec = secrets.token_hex(32)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="ascii") as f:
        f.write(sec + "\n")
    return sec.encode("ascii")


def sign(payload, secret=None):
    """payload dict → token str。"""
    sec = secret or load_secret()
    body = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    sig = hmac.new(sec, body, hashlib.sha256).digest()
    return _b64e(body) + "." + _b64e(sig)


def verify(token, scope=None, now=None, secret=None):
    """token → payload dict；非法/过期/作用域不符抛 TokenError。"""
    if not token or "." not in token:
        raise TokenError("令牌格式非法")
    a, b = token.split(".", 1)
    try:
        body = _b64d(a)
        sig = _b64d(b)
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise TokenError("令牌编码非法")
    if not isinstance(payload, dict):
        raise TokenError("令牌载荷非法")
    sec = secret or load_secret()
    expect = hmac.new(sec, body, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expect):
        raise TokenError("令牌签名不匹配")
    t = int(now if now is not None else time.time())
    if payload.get("nbf") and t < int(payload["nbf"]):
        raise TokenError("令牌尚未生效")
    if payload.get("exp") and t > int(payload["exp"]):
        raise TokenError("令牌已过期")
    if scope and payload.get("scope") != scope:
        raise TokenError("令牌作用域不符：需要 %s" % scope)
    return payload


def make_page_token(scene, id="", code="", ttl=DEFAULT_TTL, extra=None, secret=None):
    """卡片/页面令牌。"""
    now = int(time.time())
    payload = {"scope": "page", "scene": scene, "nbf": now, "exp": now + int(ttl)}
    if id:
        payload["id"] = str(id)
    if code:
        payload["code"] = str(code)
    if extra:
        payload.update({k: v for k, v in extra.items() if v is not None})
    return sign(payload, secret=secret), payload


def make_share_token(resource, id="", ttl=DEFAULT_TTL, extra=None, secret=None):
    """外部分享令牌（scope=share）。"""
    now = int(time.time())
    payload = {
        "scope": "share",
        "resource": resource,
        "nbf": now,
        "exp": now + int(ttl),
    }
    if id:
        payload["id"] = str(id)
    if extra:
        payload.update({k: v for k, v in extra.items() if v is not None})
    return sign(payload, secret=secret), payload


if __name__ == "__main__":
    tok, p = make_page_token("chan", id="605100", code="605100")
    print("token:", tok[:40] + "…")
    print("payload:", p)
    print("verify:", verify(tok, scope="page")["scene"])
