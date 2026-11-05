# -*- coding: utf-8 -*-
"""数据打通 2026-11-05（T1 后端通道组）：catalog 258 表实时元数据 / 样本只读 HTTP 出口。

GET /api/data/catalog/tables  → 表清单：过滤 / 分页 / brief|full / 全量 by_source|by_freq
GET /api/data/catalog/table   → 单表元数据 + datamart 真实样本行

约束（impl_contract_20261105.md §3/§4/§6）：
  · 只读：不写磁盘、不发 POST、不接自由路径、不拼 SQL，只调 datamart.read；
  · store / manifest 是本机绝对路径，brief 项与 meta 一律不外泄；
  · brief 不含 schema；detail=full 与 meta 才带 schema[]；
  · 复用 portal_api.Handler._json，不另造响应格式；
  · 任何异常都收敛为结构化 400 / 404 / 503 或 available=false，绝不冒泡裸 500 栈。
"""
from __future__ import annotations

import datetime
import difflib
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs

# 仿 api_dw_registry.py / api_hq.py：仓库根由 __file__ 推导，不硬编码绝对路径。
REPO = Path(__file__).resolve().parents[1]
QBT = REPO / "quant_backtest"
CATALOG_PATH = QBT / "data" / "catalog.json"
# datamart.py 使用同目录 import（tdx_read / warehouse_read_guard），
# 必须把 quant_backtest/ 加进 sys.path 才能 import datamart（仿 portal_api.py
# 顶部 QBT_DIR 的 sys.path.insert 口径）。
if str(QBT) not in sys.path:
    sys.path.insert(0, str(QBT))

_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
_D8_RE = re.compile(r"^\d{8}$")
MAX_LIST_LIMIT = 1000          # §4.1 limit 上限
MAX_SAMPLE_LIMIT = 200         # §4.2 limit 硬上限

# §4.1 brief 项白名单字段与缺省值。白名单之外的字段（尤其 store / manifest /
# schema / priority / readiness_basis ...）一律不进响应，从源头杜绝绝对路径外泄。
_BRIEF_DEFAULTS = (
    ("source", None),
    ("freq", None),
    ("rows", None),
    ("freshness", None),
    ("date_col", None),
    ("code_col", None),
    ("layout", None),
    ("files", None),
    ("primary_key", []),
    ("verification_status", None),
    ("deprecated", False),
    ("lifecycle", "active"),       # 非全表有；缺省按"活跃"口径（契约 §4.1 示例）
    ("static_no_date", False),
    ("desc", ""),
)


# ── catalog 载入 ──────────────────────────────────────────────
def _load_catalog():
    """读 catalog.json；缺失 / 损坏 / 结构异常一律抛异常，由调用方转 503。"""
    with CATALOG_PATH.open("r", encoding="utf-8") as fh:
        cat = json.load(fh)
    if not isinstance(cat, dict) or not isinstance(cat.get("tables"), dict):
        raise ValueError("catalog.json 结构异常：缺少 tables 字典")
    return cat


# ── §4.1 / §4.2 的 item 构造 ──────────────────────────────────
def _brief_item(name, rec):
    """§4.1 brief 项：仅白名单字段，绝不含 schema / store / manifest。"""
    rec = rec or {}
    item = {"name": name}
    for key, dft in _BRIEF_DEFAULTS:
        item[key] = rec.get(key, dft)
    item["primary_key"] = list(item["primary_key"] or [])
    return item


def _meta_item(name, rec):
    """§4.1 detail=full 项 / §4.2 meta：brief 项 + schema[]（仍不含 store/manifest）。"""
    item = _brief_item(name, rec)
    item["schema"] = list((rec or {}).get("schema") or [])
    return item


# ── DataFrame → JSON 类型安全转换 ──────────────────────────────
def _cell(value):
    """单元格 → JSON 安全值：NaN/NaT→None、Timestamp/date→ISO 串、
    numpy 标量→Python 标量、bytes→repr；兜底 str()。禁止直接 json.dumps(df)。"""
    if value is None:
        return None
    # NaN / NaT / pd.NA 自不等；必须早于 datetime 分派（pd.NaT 是 datetime 子类）。
    try:
        if bool(value != value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        try:
            return value.isoformat()
        except (TypeError, ValueError):
            return str(value)
    # numpy 标量（np.int64 / np.bool_ / np.float64 / np.datetime64 / np.str_ ...）
    if hasattr(value, "item") and not isinstance(
            value, (bool, int, str, bytes, bytearray)):
        try:
            return _cell(value.item())
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return None if (math.isnan(value) or math.isinf(value)) else value
    if isinstance(value, (bytes, bytearray)):
        return repr(bytes(value))[:200]
    return str(value)


def _frame_to_json(df):
    """DataFrame → (columns, rows)；逐行逐单元格过 _cell，不依赖 pandas 序列化。"""
    if df is None:
        return [], []
    columns = [str(c) for c in list(df.columns)]
    rows = []
    if bool(getattr(df, "empty", False)):
        return columns, rows
    for tup in df.itertuples(index=False, name=None):
        rows.append([_cell(v) for v in tup])
    return columns, rows


def _read_frame(name, start, end, limit):
    """唯一取数出口：只调 datamart.read(name, start=..., end=..., limit=...)。
    延迟 import（模块加载期不触发 pandas / parquet 依赖）；单测可 monkeypatch 本函数。"""
    import datamart  # noqa: PLC0415
    return datamart.read(name, start=start, end=end, limit=limit)


# ── 结构化错误响应（全部走 handler._json，不另造格式）──────────
def _bad_param(handler, message):
    handler._json({"ok": False, "error": "bad_param", "message": message}, 400)


def _catalog_unavailable(handler, exc):
    handler._json({"ok": False, "error": "catalog_unavailable",
                   "message": "catalog 不可用：%s: %s"
                              % (type(exc).__name__, str(exc)[:200])}, 503)


def _table_not_found(handler, name, tables, aliases):
    all_names = sorted(set(list(tables.keys()) + list(aliases.keys())))
    q = (name or "").lower()
    cand = [n for n in all_names if q and (q in n.lower() or n.lower().startswith(q))]
    for n in difflib.get_close_matches(name or "", all_names, n=8, cutoff=0.3):
        if n not in cand:
            cand.append(n)
    handler._json({"ok": False, "error": "table_not_found",
                   "message": "表 %r 不存在或 name 非法（须匹配 ^[A-Za-z0-9_]+$）" % name,
                   "candidates": cand[:8]}, 404)


# ── §4.1 GET /api/data/catalog/tables ─────────────────────────
def _tables(handler, get):
    detail = get("detail", "brief") or "brief"
    if detail not in ("brief", "full"):
        return _bad_param(handler, "detail 只能是 brief 或 full")
    raw_limit = get("limit", "0") or "0"
    raw_offset = get("offset", "0") or "0"
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        return _bad_param(handler, "limit 必须是整数")
    try:
        offset = int(raw_offset)
    except (TypeError, ValueError):
        return _bad_param(handler, "offset 必须是整数")
    if limit < 0:
        return _bad_param(handler, "limit 不能为负数")
    if offset < 0:
        return _bad_param(handler, "offset 不能为负数")
    if limit > MAX_LIST_LIMIT:
        limit = MAX_LIST_LIMIT

    try:
        cat = _load_catalog()
    except Exception as exc:  # noqa: BLE001 — 缺失/损坏都不假装 0 表
        return _catalog_unavailable(handler, exc)

    tables = cat.get("tables") or {}
    aliases = cat.get("aliases") or {}
    # by_source / by_freq 基于全量统计，不受过滤影响（契约 §4.1）
    by_source = dict(Counter(str((r or {}).get("source") or "") for r in tables.values()))
    by_freq = dict(Counter(str((r or {}).get("freq") or "") for r in tables.values()))

    src = get("source") or None
    freq = get("freq") or None
    q = (get("q") or "").lower()
    picked = []
    for name, rec in tables.items():
        rec = rec or {}
        if src is not None and rec.get("source") != src:
            continue
        if freq is not None and rec.get("freq") != freq:
            continue
        if q and q not in name.lower() and q not in str(rec.get("desc") or "").lower():
            continue
        picked.append((name, rec))

    total = len(picked)
    page = picked[offset:] if limit == 0 else picked[offset:offset + limit]
    build = _meta_item if detail == "full" else _brief_item
    items = [build(n, r) for n, r in page]

    handler._json({
        "ok": True,
        "endpoint": "/api/data/catalog/tables",
        "source": "catalog.json",
        "version": cat.get("version"),
        "updated": cat.get("updated"),
        "total": total,
        "returned": len(items),
        "aliases": len(aliases),
        "by_source": by_source,
        "by_freq": by_freq,
        "items": items,
    })


# ── §4.2 GET /api/data/catalog/table ──────────────────────────
def _table(handler, get):
    name = get("name")
    start = get("start") or None
    end = get("end") or None
    for label, val in (("start", start), ("end", end)):
        if val is not None and not _D8_RE.fullmatch(val):
            return _bad_param(handler, "%s 必须是 8 位 YYYYMMDD" % label)
    raw_limit = get("limit", "20") or "20"
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        return _bad_param(handler, "limit 必须是整数")
    if limit < 1:
        return _bad_param(handler, "limit 必须 >= 1")
    # 上限按「自动截断」处理而非报错（前端弹窗不因参数略大而失败），
    # 但必须把截断事实回传，避免调用方以为拿到了自己要的行数。
    requested_limit = limit
    if limit > MAX_SAMPLE_LIMIT:
        limit = MAX_SAMPLE_LIMIT

    try:
        cat = _load_catalog()
    except Exception as exc:  # noqa: BLE001
        return _catalog_unavailable(handler, exc)

    tables = cat.get("tables") or {}
    aliases = cat.get("aliases") or {}
    if not _NAME_RE.fullmatch(name or "") or (name not in tables and name not in aliases):
        return _table_not_found(handler, name, tables, aliases)

    if name in aliases:
        a = aliases.get(name) or {}
        resolved = a.get("primary") or (a.get("resolves") or [name])[0] or name
    else:
        resolved = name
    meta = _meta_item(resolved, tables.get(resolved) or tables.get(name) or {})

    payload = {
        "ok": True,
        "name": name,
        "resolved": resolved,
        "meta": meta,
        "columns": [],
        "rows": [],
        "row_count": 0,
        "truncated": False,
        "limit_applied": limit,
        "clamped": requested_limit > MAX_SAMPLE_LIMIT,
        "sample_note": "按存储顺序返回样本，非按日期倒序；需要最新数据请带 start/end",
        "available": False,
        "reason": None,
    }
    # 整段 try/except：任何取数 / 转换异常都收敛成 available=false + reason，不冒泡裸栈。
    try:
        # 别名（如 daily）若原样传给 datamart.read 会触发跨源 concat 全表扫描，
        # 实测 60s 仍不返回；样本端点按 resolves.primary 取数，并在响应里
        # 用 name/resolved 明示这层收敛，绝不静默改变语义。
        df = _read_frame(resolved, start, end, limit)
        columns, rows = _frame_to_json(df)
        payload["columns"] = columns
        payload["rows"] = rows
        payload["row_count"] = len(rows)
        payload["truncated"] = len(rows) >= limit
        payload["available"] = True
        payload["reason"] = None
    except Exception as exc:  # noqa: BLE001 — fail-visible，不假绿也不裸 500
        payload["available"] = False
        payload["reason"] = "%s: %s" % (type(exc).__name__, str(exc)[:300])
    handler._json(payload)


# ── 入口（由 portal_api.do_GET 前缀早分支调用）────────────────
def handle_get(handler, path, qs=""):
    params = parse_qs(qs or "")
    get = lambda k, d="": (params.get(k) or [d])[0].strip()  # noqa: E731
    try:
        if path == "/api/data/catalog/tables":
            _tables(handler, get)
            return
        if path == "/api/data/catalog/table":
            _table(handler, get)
            return
        handler._json({"ok": False, "error": "not_found",
                       "message": "未知 /api/data/catalog 端点"}, 404)
    except Exception as exc:  # noqa: BLE001 — 最后兜底，绝不冒泡成裸 500 栈
        try:
            handler._json({"ok": False, "error": "catalog_unavailable",
                           "message": "%s: %s" % (type(exc).__name__, str(exc)[:200])}, 503)
        except Exception:
            pass
