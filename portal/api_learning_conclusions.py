# -*- coding: utf-8 -*-
"""W3-3b 学习结论只读 API：GET /api/learning/conclusions。

由 portal_api.do_GET 早分支调用（仅 4 行路由分发；本模块独立承载逻辑）。
数据源：portal/static/learning_conclusions.json（只读，Top100；缺失返回 503 NOT_READY）。
契约：GET /api/learning/conclusions?confidence=高&executability=A&tactic=缠论&limit=20&offset=0
响应：{"ok": true, "total": N, "items": [...], "query": {...}}；参数非法 → 400 INVALID_ARGUMENT。
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "portal" / "static" / "learning_conclusions.json"
VALID_CONFIDENCE = ("高", "中", "低")
VALID_EXECUTABILITY = ("A", "B", "C")
VALID_TACTICS = ("缠论", "均线", "指标", "量价", "形态", "风控", "心理", "量化", "未分类")
DEFAULT_LIMIT = 100
MAX_LIMIT = 100

_cache = {"mtime": None, "data": None}


def load_conclusions(path: Path | str | None = None) -> dict:
    """读取学习结论静态数据（path 缺省时用模块级 STATIC，便于测试替换/降级）。"""
    p = Path(path) if path is not None else Path(STATIC)
    st = p.stat()
    if _cache["data"] is None or _cache["mtime"] != st.st_mtime_ns:
        _cache.update({"mtime": st.st_mtime_ns,
                       "data": json.loads(p.read_text(encoding="utf-8"))})
    return _cache["data"]


def _err(h, message: str, code: str = "INVALID_ARGUMENT", status: int = 400) -> None:
    h._json({"ok": False, "error": {"code": code, "message": message}}, status)


def _int_param(q: dict, name: str, default: int, lo: int, hi: int) -> int:
    raw = (q.get(name) or [str(default)])[0]
    try:
        val = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name} 必须是整数（收到 {raw!r}）")
    if val < lo or val > hi:
        raise ValueError(f"{name} 必须在 {lo}..{hi} 之间（收到 {val}）")
    return val


def filter_conclusions(data: dict, confidence: str | None = None,
                       executability: str | None = None, tactic: str | None = None,
                       limit: int = DEFAULT_LIMIT, offset: int = 0) -> dict:
    items = list(data.get("conclusions") or [])
    if confidence:
        items = [c for c in items if c.get("confidence") == confidence]
    if executability:
        items = [c for c in items
                 if ((c.get("executability") or {}).get("level")) == executability]
    if tactic:
        items = [c for c in items if ((c.get("tactic") or {}).get("category")) == tactic]
    total = len(items)
    return {"total": total, "items": items[offset:offset + limit]}


def handle_get(h, path: str, qs: str = "") -> None:
    try:
        q = parse_qs(qs or "")
        confidence = (q.get("confidence") or [None])[0]
        executability = (q.get("executability") or [None])[0]
        tactic = (q.get("tactic") or [None])[0]
        if confidence and confidence not in VALID_CONFIDENCE:
            return _err(h, f"confidence 只能是 高/中/低（收到 {confidence!r}）")
        if executability and executability not in VALID_EXECUTABILITY:
            return _err(h, f"executability 只能是 A/B/C（收到 {executability!r}）")
        if tactic and tactic not in VALID_TACTICS:
            return _err(h, f"tactic 只能是 {'/'.join(VALID_TACTICS)}（收到 {tactic!r}）")
        limit = _int_param(q, "limit", DEFAULT_LIMIT, 1, MAX_LIMIT)
        offset = _int_param(q, "offset", 0, 0, 10 ** 6)
        data = load_conclusions()
        res = filter_conclusions(data, confidence, executability, tactic, limit, offset)
        h._json({"ok": True, "total": res["total"], "items": res["items"],
                 "query": {"confidence": confidence or None,
                           "executability": executability or None,
                           "tactic": tactic or None,
                           "limit": limit, "offset": offset},
                 "schema_version": data.get("schema_version"),
                 "generated_at": data.get("generated_at")})
    except ValueError as exc:
        _err(h, str(exc))
    except FileNotFoundError:
        _err(h, "学习结论数据未生成（portal/static/learning_conclusions.json 缺失）",
             code="NOT_READY", status=503)
    except Exception as exc:  # noqa: BLE001 - 只读 API 兜底
        _err(h, f"{type(exc).__name__}: {exc}", code="INTERNAL", status=500)


__all__ = ["handle_get", "filter_conclusions", "load_conclusions",
           "VALID_CONFIDENCE", "VALID_EXECUTABILITY", "VALID_TACTICS"]
