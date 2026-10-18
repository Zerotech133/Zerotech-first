"""PBKDF2-HMAC-SHA256 密码哈希（标准库，无额外依赖）。"""
from __future__ import annotations

import base64
import hashlib
import secrets

_ITERATIONS = 260_000
_SALT_BYTES = 32


def hash_password(plain: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt, _ITERATIONS)
    return base64.b64encode(salt + digest).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        raw = base64.b64decode(hashed.encode())
        salt, stored = raw[:_SALT_BYTES], raw[_SALT_BYTES:]
        digest = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt, _ITERATIONS)
        return secrets.compare_digest(digest, stored)
    except Exception:  # noqa: BLE001
        return False
