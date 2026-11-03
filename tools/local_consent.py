#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地授权同意书（按用户、可过期、可撤销；不存原文）。

consent 存在 _local_libraries/.consent/<user>.json（隐藏目录，不进扫描）。
scope 仅允许 summary_only；expires_at 到期自动失效。
"""
from __future__ import annotations

import datetime
import json
import os
import re
import time
from pathlib import Path

BASE = Path(os.environ.get("ZB_LOCAL_LIBRARIES", r"C:\项目\_local_libraries"))
CONSENT_DIR = BASE / ".consent"
USER_RE = re.compile(r"^[A-Za-z0-9._@-]{1,80}$")
SCHEMA = "hq/local_consent/v1"
SCOPES = {"summary_only"}


def _path(user: str) -> Path:
    if not user or user in (".", "..") or not USER_RE.fullmatch(user):
        raise ValueError("bad_user")
    return CONSENT_DIR / (user + ".json")


def grant(user: str, ttl_seconds: int = 86400, scope: str = "summary_only") -> dict:
    if scope not in SCOPES:
        raise ValueError("bad_scope")
    ttl_seconds = max(60, min(int(ttl_seconds), 30 * 86400))
    now = datetime.datetime.now().astimezone()
    rec = {"schema": SCHEMA, "user_id": user, "scope": scope,
           "granted_at": now.isoformat(timespec="seconds"),
           "expires_at": (now + datetime.timedelta(seconds=ttl_seconds)).isoformat(timespec="seconds"),
           "revoked": False}
    p = _path(user)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return rec


def get(user: str) -> dict | None:
    p = _path(user)
    try:
        rec = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict) or rec.get("revoked") or rec.get("scope") not in SCOPES:
        return None
    try:
        exp = datetime.datetime.fromisoformat(rec.get("expires_at") or "")
    except ValueError:
        return None
    if exp <= datetime.datetime.now().astimezone():
        return None
    return rec


def revoke(user: str) -> dict:
    p = _path(user)
    if p.is_file():
        try:
            rec = json.loads(p.read_text(encoding="utf-8-sig"))
        except ValueError:
            rec = {}
        rec["revoked"] = True
        rec["revoked_at"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, p)
        return rec
    return {"user_id": user, "revoked": True}


def valid(user: str) -> bool:
    return get(user) is not None
