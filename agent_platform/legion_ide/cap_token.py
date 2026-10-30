# -*- coding: utf-8 -*-
"""零界本地能力令牌（Phase A）：无状态 HMAC，scope + task + TTL。

格式：base64url(payload_json) + "." + hmac_sha256_hex
payload：{"v":1,"sub":...,"task":...,"scopes":[...],"iat":...,"exp":...,"nonce":...}

master key：LEGION_CAP_KEY_FILE 或默认 C:\\项目\\_vault\\legion_ide\\cap_master.key
未配置 key = 功能关闭（verify 返回 disabled），保持现状。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

DEFAULT_KEY_FILE = r"C:\项目\_vault\legion_ide\cap_master.key"
KEY_ENV = "LEGION_CAP_KEY_FILE"


def key_path() -> Path:
    return Path(os.environ.get(KEY_ENV) or DEFAULT_KEY_FILE)


def load_key(path: Path | None = None) -> bytes:
    p = Path(path) if path else key_path()
    try:
        return p.read_bytes().strip()
    except OSError:
        return b""


def enabled(path: Path | None = None) -> bool:
    return bool(load_key(path))


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def issue(scopes, task: str = "", sub: str = "local", ttl: int = 900,
          path: Path | None = None) -> str:
    key = load_key(path)
    if not key:
        raise RuntimeError("cap key 未配置（先运行 python tools/cap.py init）")
    now = int(time.time())
    payload = {
        "v": 1,
        "sub": str(sub or "local")[:64],
        "task": str(task or "")[:120],
        "scopes": sorted({str(s) for s in scopes if str(s)}),
        "iat": now,
        "exp": now + max(30, int(ttl)),
        "nonce": secrets.token_hex(8),
    }
    body = _b64(json.dumps(payload, separators=(",", ":"),
                           ensure_ascii=False).encode("utf-8"))
    sig = hmac.new(key, body.encode("ascii"), hashlib.sha256).hexdigest()
    return body + "." + sig


def verify(token: str, required_scope: str = "", task: str = "",
           path: Path | None = None) -> dict:
    """返回 {ok, error?, claims?}；不抛异常，便于 HTTP 层映射。"""
    key = load_key(path)
    if not key:
        return {"ok": True, "disabled": True, "claims": {}}
    token = str(token or "").strip()
    if not token or "." not in token:
        return {"ok": False, "error": "cap_required"}
    body, sig = token.rsplit(".", 1)
    expect = hmac.new(key, body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expect):
        return {"ok": False, "error": "cap_invalid"}
    try:
        payload = json.loads(_unb64(body).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {"ok": False, "error": "cap_invalid"}
    if int(payload.get("exp") or 0) < int(time.time()):
        return {"ok": False, "error": "cap_expired"}
    if required_scope and required_scope not in (payload.get("scopes") or []):
        return {"ok": False, "error": "cap_scope",
                "detail": {"required": required_scope, "have": payload.get("scopes")}}
    if task and str(payload.get("task") or "") != str(task):
        return {"ok": False, "error": "cap_task",
                "detail": {"required": task, "have": payload.get("task")}}
    return {"ok": True, "claims": payload}
