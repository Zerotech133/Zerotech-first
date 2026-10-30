# -*- coding: utf-8 -*-
"""B2 OIDC runtime 配置包装层。

不修改 `auth_oidc.py`（该文件另有并行 WIP），在每次 OIDC 请求前：
- 从 vault runtime env 读取 AUTH_OIDC_MODE / client / issuer / redirect；
- 写入 auth_oidc 模块的全局变量。

默认无 runtime 文件时保持 auth_oidc 的 off。
"""
from __future__ import annotations

import os
from pathlib import Path

import auth_oidc

RUNTIME_FILE = Path(os.environ.get(
    "OIDC_RUNTIME_FILE",
    r"C:\aios\_vault\pocket-id\secrets\oidc_runtime.env",
))


def apply() -> dict:
    vals = {}
    try:
        for line in RUNTIME_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip()
    except OSError:
        return {"applied": False, "reason": "runtime_file_missing"}

    if vals.get("AUTH_OIDC_MODE"):
        auth_oidc.MODE = vals["AUTH_OIDC_MODE"].lower()
    if vals.get("OIDC_ISSUER"):
        auth_oidc.ISSUER = vals["OIDC_ISSUER"].rstrip("/")
    if vals.get("OIDC_CLIENT_ID"):
        auth_oidc.CLIENT_ID = vals["OIDC_CLIENT_ID"]
    if vals.get("OIDC_CLIENT_SECRET"):
        auth_oidc.CLIENT_SECRET = vals["OIDC_CLIENT_SECRET"]
    if vals.get("OIDC_REDIRECT_URI"):
        auth_oidc.REDIRECT_URI = vals["OIDC_REDIRECT_URI"]
    if vals.get("OIDC_SCOPES"):
        auth_oidc.SCOPES = vals["OIDC_SCOPES"]
    return {"applied": True, "mode": auth_oidc.MODE, "ready": auth_oidc._ready()}
