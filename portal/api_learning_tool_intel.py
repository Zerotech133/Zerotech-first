# -*- coding: utf-8 -*-
"""W3-4 工具情报库只读 API：GET /api/learning/tool-intel（task-161）。

数据源：agent_platform/registry/tool_intel.json（45 工具；二期工具情报库资产化，只读）。
由 portal_api.do_GET 早分支调用（仅 4 行路由分发；本模块独立承载逻辑）。

契约：
  GET /api/learning/tool-intel
      ?category=行情|研报|量化|回测|绘图|Agent
      &confidence=高|中|低
      &q=<关键词>            # 名称/描述/能力/别名
      &limit=1..100（默认 100）&offset=0..1e6
响应：{"ok": true, "count": N, "total": M, "by_category": {...}, "by_confidence": {...},
       "items": [...], "query": {...}, "schema_version", "generated_at", "source"}
错误：参数非法 → 400 INVALID_ARGUMENT；registry 缺失 → 503 NOT_READY；其它 → 500 INTERNAL。
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "agent_platform" / "registry" / "tool_intel.json"
VALID_CATEGORIES = ("行情", "研报", "量化", "回测", "绘图", "Agent")
VALID_CONFIDENCE = ("高", "中", "低")
DEFAULT_LIMIT = 100
MAX_LIMIT = 100

_cache = {"mtime": None, "data": None}


def load_tool_intel(path: Path | str | None = None) -> dict:
    """读取工具情报 registry（path 缺省为模块级 REGISTRY；便于测试替换/降级）。"""
    p = Path(path) if path is not None else Path(REGISTRY)
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


def filter_tools(data: dict, category: str | None = None, confidence: str | None = None,
                 q: str | None = None, limit: int = DEFAULT_LIMIT, offset: int = 0) -> dict:
    items = [t for t in (data.get("tools") or []) if isinstance(t, dict)]
    if category:
        items = [t for t in items if t.get("category") == category]
    if confidence:
        items = [t for t in items if t.get("confidence") == confidence]
    if q:
        ql = q.lower()

        def hit(t: dict) -> bool:
            hay = " ".join([
                str(t.get("name") or ""), str(t.get("description") or ""),
                " ".join(str(x) for x in (t.get("capabilities") or [])),
                " ".join(str(x) for x in (t.get("aliases") or [])),
                str(t.get("address") or ""),
            ]).lower()
            return ql in hay
        items = [t for t in items if hit(t)]
    total = len(items)
    return {"total": total, "items": items[offset:offset + limit]}


def handle_get(h, path: str = "/api/learning/tool-intel", qs: str = "") -> None:
    try:
        q = parse_qs(qs or "")
        category = (q.get("category") or [None])[0]
        confidence = (q.get("confidence") or [None])[0]
        keyword = (q.get("q") or [None])[0]
        if category and category not in VALID_CATEGORIES:
            return _err(h, f"category 只能是 {'/'.join(VALID_CATEGORIES)}（收到 {category!r}）")
        if confidence and confidence not in VALID_CONFIDENCE:
            return _err(h, f"confidence 只能是 高/中/低（收到 {confidence!r}）")
        limit = _int_param(q, "limit", DEFAULT_LIMIT, 1, MAX_LIMIT)
        offset = _int_param(q, "offset", 0, 0, 10 ** 6)
        data = load_tool_intel()
        res = filter_tools(data, category, confidence, keyword, limit, offset)
        h._json({
            "ok": True,
            "count": data.get("count"),
            "total": res["total"],
            "items": res["items"],
            "by_category": data.get("by_category") or {},
            "by_confidence": data.get("by_confidence") or {},
            "query": {"category": category or None, "confidence": confidence or None,
                      "q": keyword or None, "limit": limit, "offset": offset},
            "schema_version": data.get("schema_version"),
            "generated_at": data.get("generated_at"),
            "source": data.get("source") or {},
        })
    except ValueError as exc:
        _err(h, str(exc))
    except FileNotFoundError:
        _err(h, "工具情报库数据未就绪（agent_platform/registry/tool_intel.json 缺失）",
             code="NOT_READY", status=503)
    except Exception as exc:  # noqa: BLE001 - 只读 API 兜底
        _err(h, f"{type(exc).__name__}: {exc}", code="INTERNAL", status=500)


__all__ = ["handle_get", "filter_tools", "load_tool_intel",
           "VALID_CATEGORIES", "VALID_CONFIDENCE"]
