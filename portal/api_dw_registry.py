# -*- coding: utf-8 -*-
"""数据仓统一取数 HTTP 入口（WP-I3，独立 handler 文件）。

GET /api/dw/stats              → 数据集总量/登记/入口/分域
GET /api/dw/list?domain=&q=&registered=0|1&exposed=0|1&limit=  → 数据集列表
GET /api/dw/get?id=<dataset_id> → 数据集元数据
GET /api/dw/sample?id=&n=      → 数据集样本（sqlite/parquet/json/目录；只读）
GET /api/dw/search?q=&limit=   → 关键词检索数据集

纪律：只读；路径白名单在 dataset_registry.ALLOWED_ROOTS；不写业务数据；
注意：新增路由需重启 8091 才生效（重启属 L2，需批准）。
"""
from __future__ import annotations

import sys
import urllib.parse
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools" / "datawarehouse"))

from dataset_registry import DatasetRegistry  # noqa: E402

_REG = None


def _reg() -> DatasetRegistry:
    global _REG
    if _REG is None:
        _REG = DatasetRegistry()
    return _REG


def _int(v, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def _bool(v):
    if v in (None, ""):
        return None
    return v in ("1", "true", "True", "yes")


def handle_get(handler, path: str, qs: str) -> None:
    try:
        params = urllib.parse.parse_qs(qs or "")
        get = lambda k, d="": (params.get(k) or [d])[0].strip()  # noqa: E731
        if path == "/api/dw/stats":
            handler._json({"ok": True, **_reg().stats()})
            return
        if path == "/api/dw/list":
            rows = _reg().list(domain=get("domain") or None, q=get("q") or None,
                               registered=_bool(get("registered")), exposed=_bool(get("exposed")),
                               limit=_int(get("limit"), 50, 1, 200))
            handler._json({"ok": True, "count": len(rows), "datasets": rows})
            return
        if path == "/api/dw/search":
            q = get("q")
            if not q:
                handler._json({"ok": False, "error": "q 必填"}, 400)
                return
            rows = _reg().list(q=q, limit=_int(get("limit"), 20, 1, 200))
            handler._json({"ok": True, "count": len(rows), "datasets": rows})
            return
        if path == "/api/dw/get":
            did = get("id")
            if not did:
                handler._json({"ok": False, "error": "id 必填"}, 400)
                return
            d = _reg().get(did)
            if d is None:
                handler._json({"ok": False, "error": "dataset 不存在", "id": did}, 404)
                return
            handler._json({"ok": True, "dataset": d})
            return
        if path == "/api/dw/sample":
            did = get("id")
            if not did:
                handler._json({"ok": False, "error": "id 必填"}, 400)
                return
            obj = _reg().sample(did, n=_int(get("n"), 3, 1, 20))
            handler._json({"ok": True, **obj})
            return
        handler._json({"ok": False, "error": "not found"}, 404)
    except KeyError as exc:  # noqa: BLE001
        handler._json({"ok": False, "error": "dataset 不存在: %s" % str(exc)[:120]}, 404)
    except PermissionError as exc:  # noqa: BLE001
        handler._json({"ok": False, "error": "路径越权: %s" % str(exc)[:120]}, 403)
    except Exception as exc:  # noqa: BLE001 — fail-visible
        handler._json({"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}, 503)
