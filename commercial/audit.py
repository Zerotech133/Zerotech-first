# -*- coding: utf-8 -*-
"""审计日志：商业动作追加 JSONL（可导出对账/合规）。"""
import json
import os
import time
from typing import Any, Dict, List, Optional

from . import tenants

AUDIT_FILE = os.environ.get("COMMERCIAL_AUDIT") or os.path.join(tenants.BASE, "audit", "audit.jsonl")


def _path(path: Optional[str] = None) -> str:
    p = path or AUDIT_FILE
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def append(action: str, tenant: str = "", actor: str = "system", resource: str = "",
           result: str = "ok", detail: Optional[Dict[str, Any]] = None,
           path: Optional[str] = None) -> Dict[str, Any]:
    rec = {
        "ts": time.strftime("%F %T"),
        "tenant": tenant,
        "actor": actor,
        "action": action,
        "resource": resource,
        "result": result,
        "detail": detail or {},
    }
    with open(_path(path), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def tail(n: int = 20, path: Optional[str] = None, tenant: Optional[str] = None) -> List[Dict[str, Any]]:
    p = _path(path)
    if not os.path.exists(p):
        return []
    out: List[Dict[str, Any]] = []
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if tenant and rec.get("tenant") != tenant:
                continue
            out.append(rec)
    return out[-n:]


def export(dest: str, path: Optional[str] = None) -> int:
    """导出全部审计为文件（企业版审计导出）。"""
    p = _path(path)
    if not os.path.exists(p):
        open(dest, "w", encoding="utf-8").close()
        return 0
    n = 0
    with open(p, "r", encoding="utf-8") as src, open(dest, "w", encoding="utf-8") as dst:
        for line in src:
            dst.write(line)
            n += 1
    return n
