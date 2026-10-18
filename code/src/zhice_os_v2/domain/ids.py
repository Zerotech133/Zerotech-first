"""ID 长度适配：超 maxlen 时保留可读前缀 + md5 尾缀压缩，保证唯一且 ≤maxlen。"""
from __future__ import annotations

import hashlib


def fit_id(prefix: str, body: str, maxlen: int = 64) -> str:
    """`{prefix}{body}` 超 maxlen 时压缩为 `{prefix}{body[:keep]}-{md5[:10]}`（长度=maxlen）。"""
    s = f"{prefix}{body}"
    if len(s) <= maxlen:
        return s
    h = hashlib.md5(s.encode()).hexdigest()[:10]
    keep = maxlen - len(prefix) - 11  # 11 = '-' + 10位hash
    return f"{prefix}{body[:keep]}-{h}"
