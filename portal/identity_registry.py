# -*- coding: utf-8 -*-
"""IdentityContext registry resolver（P3 统一身份，只读）。

职责：
- 读取 `agent_platform/identity/identity_registry.json`；
- 按 sub / (source, source_id) 解析成符合 identity_context schema 的上下文；
- 列出已映射 actor（脱敏）。

红线：
- 不鉴权：`roles/permissions` 是描述，不是授权；
- 不合并未确认身份：`links[].status=unconfirmed` 只展示，不生成跨系统权限；
- 不写库、不读密钥、不发起写请求。

用法：
  python tools/identity_context.py --list
  python tools/identity_context.py --resolve zero_bound:owner
  python tools/identity_context.py --source zhice --id u-96d51f4d9b7a2826
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

BEIJING = timezone(timedelta(hours=8))
HERE = Path(__file__).resolve().parent
REGISTRY_PATH = HERE.parent / "agent_platform" / "identity" / "identity_registry.json"

if not REGISTRY_PATH.is_file():
    # 兼容从 portal 目录/打包路径启动
    alt = Path(os.environ.get("IDENTITY_REGISTRY_PATH", ""))
    if alt.is_file():
        REGISTRY_PATH = alt


def _now() -> str:
    return datetime.now(BEIJING).isoformat(timespec="seconds")


def load_registry(path: Path | None = None) -> Dict[str, Any]:
    p = Path(path) if path is not None else REGISTRY_PATH
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("actors"), list):
        raise ValueError("identity_registry_invalid")
    return data


def list_actors(registry: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
    """安全列表：不含凭证/密码；links 只保留状态与证据。"""
    reg = registry or load_registry()
    out = []
    for a in reg["actors"]:
        out.append({
            "sub": a.get("sub"),
            "display_name": a.get("display_name", ""),
            "actor_type": a.get("actor_type"),
            "source": a.get("source"),
            "roles": a.get("roles", []),
            "permissions": a.get("permissions", []),
            "owner_scope": a.get("owner_scope"),
            "mapping_status": a.get("mapping_status", "unknown"),
            "links": [
                {"system": l.get("system"), "source_id": l.get("source_id"),
                 "status": l.get("status"), "evidence": l.get("evidence", "")}
                for l in a.get("links", [])
            ],
        })
    return out


def build_context(record: Dict[str, Any]) -> Dict[str, Any]:
    """把注册记录转成 IdentityContext（只描述，不授权）。"""
    ctx = {
        "sub": record["sub"],
        "actor_type": record["actor_type"],
        "tenant_id": record.get("tenant_id"),
        "roles": record.get("roles", []),
        "permissions": record.get("permissions", []),
        "owner_scope": record.get("owner_scope", "own"),
        "display_name": record.get("display_name", ""),
        "source": record["source"],
        "source_ids": record.get("source_ids", {}),
        "entitlements": record.get("entitlements", []),
        "issued_at": _now(),
        "expires_at": None,
        "attributes": {
            "mapping_status": record.get("mapping_status", "unknown"),
            "links": record.get("links", []),
            "source_claims": record.get("source_claims", {}),
            "note": "descriptive only; not an authorization decision",
        },
    }
    return ctx


def resolve(sub: str, registry: Dict[str, Any] | None = None) -> Dict[str, Any] | None:
    reg = registry or load_registry()
    for a in reg["actors"]:
        if a.get("sub") == sub:
            return build_context(a)
    return None


def resolve_source(source: str, source_id: str,
                   registry: Dict[str, Any] | None = None) -> Dict[str, Any] | None:
    """按来源系统 + 外部 ID 反查 actor（只认 verified 链接）。"""
    reg = registry or load_registry()
    for a in reg["actors"]:
        for link in a.get("links", []):
            if (link.get("system") == source and link.get("source_id") == source_id
                    and link.get("status") == "verified"):
                return build_context(a)
    return None
