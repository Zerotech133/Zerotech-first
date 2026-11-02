# -*- coding: utf-8 -*-
"""数据仓统一取数 HTTP 入口（WP-I3，独立 handler 文件）。

GET /api/dw/stats              → 数据集总量/登记/入口/分域
GET /api/dw/list?domain=&q=&registered=0|1&exposed=0|1&limit=  → 数据集列表
GET /api/dw/get?id=<dataset_id> → 数据集元数据
GET /api/dw/sample?id=&n=      → 数据集样本（sqlite/parquet/json/目录；只读）
GET /api/dw/search?q=&limit=   → 关键词检索数据集

DWH-105（2026-11-02）：响应显式带数据时点与空结果三态，避免"空结果被误读为无数据/无机会"。
- data_date：盘点数据时点（dataset_inventory 文件 mtime 的日期，YYYY-MM-DD）；
- state：ok / no_data（查询为空、样本无行、路径缺失）/ not_found（dataset_id 未登记）/ stale
  （data_date 距今天数 > STALE_DAYS）；
- 非 ok 时带 reason；错误体（4xx/5xx）也带 data_date + reason。

纪律：只读；路径白名单在 dataset_registry.ALLOWED_ROOTS；不写业务数据；
注意：新增路由需重启 8091 才生效（重启属 L2，需批准）。
"""
from __future__ import annotations

import datetime
import sys
import urllib.parse
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools" / "datawarehouse"))

from dataset_registry import DatasetRegistry  # noqa: E402

STALE_DAYS = 7  # 盘点时点超过该天数 → state=stale（可配，测试通过 monkeypatch 固定）
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


def _data_date():
    """盘点数据时点：dataset_inventory 文件 mtime 的日期（YYYY-MM-DD）。"""
    try:
        return datetime.date.fromtimestamp(
            Path(str(_reg().inventory_path)).stat().st_mtime).isoformat()
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _stale_reason(data_date) -> str:
    try:
        age = (datetime.date.today() - datetime.date.fromisoformat(str(data_date))).days
    except (TypeError, ValueError):
        return ""
    if age > STALE_DAYS:
        return "数据时点 %s 距今 %d 天，超过 %d 天阈值（盘点可能过期，请先刷新盘点）" % (
            data_date, age, STALE_DAYS)
    return ""


def _envelope(state: str, reason: str = "", data_date=None) -> dict:
    out = {"data_date": data_date if data_date is not None else _data_date(),
           "state": state}
    if reason:
        out["reason"] = reason
    return out


def _ok_envelope(data_date) -> dict:
    reason = _stale_reason(data_date)
    return _envelope("stale" if reason else "ok", reason, data_date)


def _fail(error: str, code: int, reason: str = "", state: str = "") -> tuple[dict, int]:
    payload = {"ok": False, "error": error, **_envelope(state or "ok", reason or error)}
    if not state:
        payload.pop("state", None)
    return payload, code


def _sample_empty_reason(obj) -> str:
    if not isinstance(obj, dict):
        return "no_data: 样本返回为空"
    if obj.get("exists") is False:
        return "no_data: 数据集路径不存在（exists=false）：%s" % (obj.get("path") or "-")
    for key in ("sample", "rows"):
        if isinstance(obj.get(key), list) and not obj[key]:
            return "no_data: 样本为空（0 行）"
    if isinstance(obj.get("top_entries"), list) and not obj["top_entries"] \
            and not obj.get("newest_files"):
        return "no_data: 目录为空（0 个顶层条目/文件）"
    return ""


def _empty_list_reason(q=None, domain=None, registered=None, exposed=None) -> str:
    bits = []
    if q:
        bits.append("q=%r" % q)
    if domain:
        bits.append("domain=%r" % domain)
    if registered is not None:
        bits.append("registered=%d" % int(registered))
    if exposed is not None:
        bits.append("exposed=%d" % int(exposed))
    return "no_data: 无匹配数据集（%s）" % ("，".join(bits) or "无过滤条件")


def handle_get(handler, path: str, qs: str) -> None:
    params = urllib.parse.parse_qs(qs or "")
    get = lambda k, d="": (params.get(k) or [d])[0].strip()  # noqa: E731
    try:
        if path == "/api/dw/stats":
            handler._json({"ok": True, **_ok_envelope(_data_date()), **_reg().stats()})
            return
        if path == "/api/dw/list":
            domain = get("domain") or None
            q = get("q") or None
            registered = _bool(get("registered"))
            exposed = _bool(get("exposed"))
            rows = _reg().list(domain=domain, q=q, registered=registered,
                               exposed=exposed,
                               limit=_int(get("limit"), 50, 1, 200))
            dd = _data_date()
            if not rows:
                handler._json({"ok": True, "count": 0, "datasets": [],
                               **_envelope("no_data",
                                           _empty_list_reason(q, domain, registered, exposed), dd)})
                return
            handler._json({"ok": True, "count": len(rows), "datasets": rows,
                           **_ok_envelope(dd)})
            return
        if path == "/api/dw/search":
            q = get("q")
            if not q:
                handler._json(*_fail("q 必填", 400, reason="missing_param: q 必填"))
                return
            limit = _int(get("limit"), 20, 1, 200)
            rows = _reg().list(q=q, limit=limit)
            dd = _data_date()
            if not rows:
                handler._json({"ok": True, "count": 0, "datasets": [],
                               **_envelope("no_data", _empty_list_reason(q=q), dd)})
                return
            handler._json({"ok": True, "count": len(rows), "datasets": rows,
                           **_ok_envelope(dd)})
            return
        if path == "/api/dw/get":
            did = get("id")
            if not did:
                handler._json(*_fail("id 必填", 400, reason="missing_param: id 必填"))
                return
            d = _reg().get(did)
            dd = _data_date()
            if d is None:
                handler._json(*_fail("dataset 不存在", 404, state="not_found",
                                     reason="not_found: dataset_id=%r 未登记或未通过盘点过滤" % did))
                return
            handler._json({"ok": True, "dataset": d, **_ok_envelope(dd)})
            return
        if path == "/api/dw/sample":
            did = get("id")
            if not did:
                handler._json(*_fail("id 必填", 400, reason="missing_param: id 必填"))
                return
            obj = _reg().sample(did, n=_int(get("n"), 3, 1, 20))
            dd = _data_date()
            empty = _sample_empty_reason(obj)
            if empty:
                handler._json({"ok": True, **obj, **_envelope("no_data", empty, dd)})
                return
            handler._json({"ok": True, **obj, **_ok_envelope(dd)})
            return
        handler._json(*_fail("not found", 404, state="not_found",
                             reason="not_found: 未知 /api/dw 端点 %r" % path))
    except KeyError as exc:  # noqa: BLE001
        handler._json(*_fail("dataset 不存在: %s" % str(exc)[:120], 404, state="not_found",
                             reason="not_found: %s" % str(exc)[:160]))
    except PermissionError as exc:  # noqa: BLE001
        handler._json(*_fail("路径越权: %s" % str(exc)[:120], 403,
                             reason="permission_denied: %s" % str(exc)[:160]))
    except Exception as exc:  # noqa: BLE001 — fail-visible
        handler._json(*_fail("%s: %s" % (type(exc).__name__, str(exc)[:200]), 503,
                             reason="internal_error: %s: %s" % (type(exc).__name__, str(exc)[:160])))
