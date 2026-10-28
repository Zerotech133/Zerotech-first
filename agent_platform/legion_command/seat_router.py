# -*- coding: utf-8 -*-
"""seat_router：模型别名 + 席位路由表 + 虚拟 Key（网关统一接入层）。

- 别名：auto / strong / local（可扩展）
- 虚拟 Key：vk-...，仅存 sha256，页面/日志不回明文；用于归属/限额/审计
- 路由表：config/seat_routing.json（别名候选席位 + 是否允许本地兜底 + 席位默认别名）
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
import uuid
from pathlib import Path
import logging

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
CONFIG = REPO / "config" / "seat_routing.json"
STORE = REPO / "work" / "router" / "virtual_keys.json"

DEFAULT_CONFIG = {
    "schema": "seat_routing/v1",
    "aliases": {
        "auto": {"seats": ["relay", "tianfeng", "zhaoyun", "zhuge"], "allow_local": True},
        "strong": {"seats": ["relay", "tianfeng", "zhaoyun", "zhuge"], "allow_local": False},
        "local": {"seats": [], "allow_local": True},
    },
    "seats": {
        "tianfeng": "strong", "zhaoyun": "strong", "zhuge": "strong", "relay": "strong",
    },
}


def load_config(path: Path | None = None) -> dict:
    p = Path(path) if path else CONFIG
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(d, dict) and d.get("aliases"):
            return d
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/legion_command/seat_router.py", exc)
    return json.loads(json.dumps(DEFAULT_CONFIG))


def _load_vk(path: Path | None = None) -> list:
    p = Path(path) if path else STORE
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _save_vk(rows: list, path: Path | None = None) -> None:
    p = Path(path) if path else STORE
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def _hash(token: str) -> str:
    return hashlib.sha256(str(token).encode()).hexdigest()


def create_vk(owner: str, alias: str = "auto", allowed=None,
              path: Path | None = None) -> dict:
    owner = str(owner or "").strip()
    if not owner:
        raise ValueError("owner 必填")
    cfg = load_config()
    aliases = list((cfg.get("aliases") or {}).keys())
    if alias not in aliases:
        raise ValueError("alias 必须是 %s" % aliases)
    allowed = [a for a in (allowed or aliases) if a in aliases]
    if not allowed:
        raise ValueError("allowed 为空")
    token = "vk-" + secrets.token_urlsafe(24)
    rows = _load_vk(path)
    item = {"schema": "virtual_key/v1", "id": "vk-" + uuid.uuid4().hex[:8],
            "owner": owner, "alias": alias, "allowed": allowed,
            "token_sha256": _hash(token), "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "enabled": True, "last_used": None, "masked": token[:6] + "***" + token[-4:]}
    rows.append(item)
    _save_vk(rows, path)
    out = dict(item)
    out.pop("token_sha256", None)
    out["token"] = token  # 仅创建时返回一次
    return out


def list_vk(path: Path | None = None) -> list:
    rows = _load_vk(path)
    return [{k: v for k, v in r.items() if k != "token_sha256"} for r in rows]


def disable_vk(vk_id: str, path: Path | None = None) -> dict:
    rows = _load_vk(path)
    for r in rows:
        if r.get("id") == vk_id:
            r["enabled"] = False
            _save_vk(rows, path)
            out = dict(r)
            out.pop("token_sha256", None)
            return out
    raise ValueError("virtual key not found: %s" % vk_id)


def _match_vk(token: str, path: Path | None = None):
    h = _hash(token)
    for r in _load_vk(path):
        if r.get("enabled") and r.get("token_sha256") == h:
            r["last_used"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            return r
    return None


def _key_seat_ids() -> set:
    try:
        d = json.loads((REPO / "config" / "key_seats.json").read_text(encoding="utf-8"))
        return set((d.get("seats") or {}).keys())
    except Exception:
        return set()


def resolve(model: str = "", token: str = "", path: Path | None = None) -> dict:
    cfg = load_config()
    aliases = cfg.get("aliases") or {}
    vk = None
    if token and str(token).startswith("vk-"):
        vk = _match_vk(token, path)
        if not vk:
            return {"ok": False, "error": "invalid_or_disabled_virtual_key", "code": 401}
    req = str(model or "").strip()
    if req and req not in aliases:
        # 非别名：按虚拟 Key 默认别名处理（模型名由上游 provider 决定）
        req = ""
    alias = req
    if not alias:
        if vk:
            alias = vk.get("alias") or "auto"
            if vk.get("owner") in (cfg.get("seats") or {}):
                alias = cfg["seats"][vk["owner"]]
        else:
            alias = "auto"
    if vk and alias not in (vk.get("allowed") or []):
        return {"ok": False, "error": "alias_not_allowed_for_vk: %s" % alias, "code": 403}
    spec = aliases.get(alias) or aliases.get("auto") or {}
    seats = list(spec.get("seats") or [])
    owner = (vk or {}).get("owner")
    if owner and owner in _key_seat_ids() and owner not in seats:
        seats = [owner] + seats
    return {"ok": True, "alias": alias,
            "seats": seats,
            "model": str(spec.get("model") or ""),
            "allow_local": bool(spec.get("allow_local", True)),
            "local_model": spec.get("local_model") or "",
            "owner": (vk or {}).get("owner"),
            "vk_id": (vk or {}).get("id")}
