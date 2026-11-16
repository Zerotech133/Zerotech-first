# -*- coding: utf-8 -*-
"""零界本地能力令牌（Phase A）：无状态 HMAC，scope + task + TTL。

格式：base64url(payload_json) + "." + hmac_sha256_hex
payload：{"v":1,"sub":...,"task":...,"scopes":[...],"iat":...,"exp":...,"nonce":...}

master key：环境变量 LEGION_CAP_KEY_FILE（或仓库根 .env.local 同名项）
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

def _load_env_local() -> None:
    """把仓库根 .env.local 里的键读进 os.environ（不覆盖已存在的）。

    2026-11-16：cap 主密钥路径原先硬编码成绝对路径，会随 push 暴露本机
    目录结构（vault_path_inventory_20261116 A 级第 7 条）。改为纯环境变量，
    并在此自补 .env.local（该文件被 .gitignore 的 `.env.*` 忽略）。
    """
    for anc in Path(__file__).resolve().parents:
        f = anc / ".env.local"
        if not f.exists():
            continue
        try:
            for raw in f.read_text(encoding="utf-8-sig").splitlines():
                raw = raw.strip()
                if not raw or raw.startswith("#") or "=" not in raw:
                    continue
                k, v = raw.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and v and k not in os.environ:
                    os.environ[k] = v
        except OSError:
            pass
        return


_load_env_local()
KEY_ENV = "LEGION_CAP_KEY_FILE"
# 缺配置时为空串：Path("") 读不到字节 -> load_key() 返回 b"" -> enabled() 为 False，
# 与原先「文件不存在」的语义完全一致（签名功能关闭，不静默使用错误密钥）。
DEFAULT_KEY_FILE = os.environ.get(KEY_ENV, "")


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
