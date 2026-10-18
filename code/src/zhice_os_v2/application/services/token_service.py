"""签名 Token 服务（轻量 JWT 风格，不依赖外部库）。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from zhice_os_v2.config.settings import get_settings

# 读取配置中的 JWT 密钥（生产环境应通过 SECRET_KEY 环境变量注入）。
_SECRET_KEY = get_settings().secret_key


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def create_token(
    user_id: str,
    username: str,
    role: str,
    auth_version: int = 1,
    *,
    secret: str = _SECRET_KEY,
    expires_in: int = 3600,
) -> str:
    header = _b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    # 单次读取签发时刻，避免 iat 与 exp 跨秒。
    issued_at = int(time.time())
    payload = _b64encode(
        json.dumps(
            {
                "user_id": user_id,
                "username": username,
                "role": role,
                # 单元 A：会话吊销代数。与 v2_user.auth_version 不一致即 401
                # （见 dependencies.resolve_token_identity）。
                "auth_version": auth_version,
                # 3c：签发时刻（秒）。resolve_token_identity 用它与 v2_user.password_changed_at
                # 比较——改密前签发的 token 一律 401。旧 token 无 iat：在该用户
                # password_changed_at 非 NULL 时同样 401。
                "iat": issued_at,
                "exp": issued_at + expires_in,
            }
        ).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    signature = _b64encode(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


def decode_token(token: str, secret: str = _SECRET_KEY) -> dict[str, Any] | None:
    try:
        header, payload, signature = token.split(".")
        signing_input = f"{header}.{payload}".encode()
        expected = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
        actual = _b64decode(signature)
        if not hmac.compare_digest(expected, actual):
            return None
        data = json.loads(_b64decode(payload))
        if int(data.get("exp", 0)) < int(time.time()):
            return None
        return data
    except Exception:
        return None
