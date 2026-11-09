# -*- coding: utf-8 -*-
"""P0-E 2026-11-06：数据治理四端点只读出口（SLA / 上游健康度 / lifecycle / 源登记）。

GET /api/data/governance/sla              → govern.audit() 的超 SLA 表（scope=stale_only）
GET /api/data/governance/upstream-health  → 最新 upstream_health_*.json 报告快照
GET /api/data/governance/lifecycle        → catalog.json 的 lifecycle / lifecycle_reason
GET /api/data/governance/sources          → sources.yaml 经 registry 的 31 源登记摘要

约束（p0e_governance_api_contract §3.5/§3.6、impl_round5_contract §2.3）：
  · 只读：不写盘、不发 POST、不拼 SQL / 文件路径、不接路径或任意表名；
  · 参数只收白名单枚举与非负整数（limit 上限 500，越界即 400 bad_param）；
  · 响应不出现本机绝对路径：catalog 的 store / manifest、快照文件全路径一律剔除，
    source 字段只放文件名或相对口径；
  · audit() 是 10-30s 重操作 → 进程内 60s TTL 缓存；命中回 cached=true 且
    generated_at 保持首次计算时点（不刷新成当前时间，防「假新鲜度信号」）；
  · 文件缺失 / 损坏 / 解析失败 / 导入失败 → 503 governance_unavailable，绝不假装 0 条；
  · 任何异常都收敛为结构化 400 / 503，绝不冒泡裸 500 栈。
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs

# 仓库根由 __file__ 推导（portal/ 的上一级），不硬编码绝对路径。
REPO = Path(__file__).resolve().parents[1]
UPSTREAM_DIRS = (
    REPO / "reports" / "data_hub",
    REPO / "quant_backtest" / "reports" / "data_hub",
    REPO / "quant_backtest" / "reports",
)
CATALOG_PATH = REPO / "quant_backtest" / "data" / "catalog.json"

EP_SLA = "/api/data/governance/sla"
EP_UPSTREAM = "/api/data/governance/upstream-health"
EP_LIFECYCLE = "/api/data/governance/lifecycle"
EP_SOURCES = "/api/data/governance/sources"
ENDPOINTS = (EP_SLA, EP_UPSTREAM, EP_LIFECYCLE, EP_SOURCES)

MAX_LIMIT = 500                 # impl_round5_contract §2.3：?limit= 硬上限
CACHE_TTL = 60.0                # 秒；audit() 约 10-30s，必须缓存

GRADES = ("stopped", "broken", "lagging")
STATES = ("ACTIVE", "STALLED", "GONE", "UNDECIDABLE")
VERDICTS = (
    "OK", "BEHIND", "FUTURE", "STATIC", "STALLED", "STALLED_WEAK",
    "GONE", "UNDECIDABLE", "CALIBER", "NO_LINK", "NO_REMOTE", "LOCAL_NEWER",
)
LIFECYCLE_STATUS = ("deprecated", "planned", "active")

# 展示层四态归并（逐表 verdict 原样保留，归并只用于概览，见契约 §3.2）
VERDICT_STATE = {
    "OK": "ACTIVE", "BEHIND": "ACTIVE", "FUTURE": "ACTIVE", "STATIC": "ACTIVE",
    "STALLED": "STALLED", "STALLED_WEAK": "STALLED",
    "GONE": "GONE",
    "UNDECIDABLE": "UNDECIDABLE", "CALIBER": "UNDECIDABLE",
    "NO_LINK": "UNDECIDABLE", "NO_REMOTE": "UNDECIDABLE",
    "LOCAL_NEWER": "UNDECIDABLE",
}

SLA_NOTE = "stale 仅含超出频率 SLA 的表，非全表新鲜度总览；同源同日止步不等于批次未跑。"
BATCH_NOTE = "batch_profile 回答『哪些表没被最近批次覆盖』，不能读成『采集挂了』。"
TOTAL_EXACT_NOTE = (
    "xtcloud /api/data/table 的 total_exact=false 时 remote_rows 为估算下限（48 张表受影响），"
    "非精确行数；本响应为报告快照，非实时探测。"
)

# 进程内 TTL 缓存：{endpoint: {"ts": monotonic, "generated_at": iso, "data": {...}}}
_CACHE: dict[str, dict] = {}

_SCRUB_PATTERNS = (
    re.compile(r"[A-Za-z]:[\\/][^\s'\",;)]*"),
    re.compile(r"/[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)+"),
)


class _BadParam(Exception):
    """参数非法 → 400 bad_param（消息固定，不回显用户输入）。"""


class _Unavailable(Exception):
    """数据源缺失 / 损坏 / 结构异常 → 503 governance_unavailable。"""


# ── 通用工具 ──────────────────────────────────────────────────
def _now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _scrub(text):
    """抹掉消息里的本机绝对路径 / 多段路径，防 detail 外泄（§3.6 红线 1）。"""
    out = str(text)
    for pat in _SCRUB_PATTERNS:
        out = pat.sub("<path>", out)
    return out[:200]


def _cached(key, loader):
    """返回 (data, generated_at, cached)。命中不刷新 generated_at。"""
    ent = _CACHE.get(key)
    if ent is not None and (time.monotonic() - ent["ts"]) <= CACHE_TTL:
        return ent["data"], ent["generated_at"], True
    data = loader()
    gen = _now_iso()
    _CACHE[key] = {"ts": time.monotonic(), "generated_at": gen, "data": data}
    return data, gen, False


def _int_param(get, name):
    raw = get(name)
    if raw is None or raw == "":
        return None
    try:
        val = int(raw)
    except (TypeError, ValueError):
        raise _BadParam("%s 必须是整数" % name)
    if val < 0:
        raise _BadParam("%s 不能为负数" % name)
    if name == "limit" and val > MAX_LIMIT:
        raise _BadParam("limit 不能超过 %d" % MAX_LIMIT)
    return val


def _range(get):
    """解析 limit / offset（0 或 None 视为不限制）。"""
    limit = _int_param(get, "limit")
    offset = _int_param(get, "offset")
    return (limit or 0), (offset or 0)


def _page(items, limit, offset):
    return items[offset:] if not limit else items[offset:offset + limit]


def _enum_param(get, name, allowed):
    raw = get(name)
    if raw is None or raw == "":
        return None
    if raw not in allowed:
        raise _BadParam("%s 取值不在白名单内" % name)
    return raw


def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise _Unavailable("%s: %s" % (type(exc).__name__, _scrub(exc)))


def _latest_upstream_path():
    """mtime 最新的 upstream_health_*.json；三级目录依次回退，全无则 503。"""
    for folder in UPSTREAM_DIRS:
        if not folder.is_dir():
            continue
        files = [p for p in glob.glob(str(folder / "upstream_health_*.json"))
                 if os.path.isfile(p)]
        if files:
            return max(files, key=os.path.getmtime)
    raise _Unavailable("upstream_health 快照不存在")


# ── 1. SLA（govern.audit()）────────────────────────────────────
def _sla_loader():
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    try:
        from tools.data_hub import govern  # noqa: PLC0415 — 包内模块，需仓库根在 sys.path
        rep = govern.audit()
    except Exception as exc:  # noqa: BLE001 — 导入/取数失败一律 503，不假装 0 条
        raise _Unavailable("%s: %s" % (type(exc).__name__, _scrub(exc)))
    if not isinstance(rep, dict) or not isinstance(rep.get("stale"), list):
        raise _Unavailable("audit() 返回结构异常：缺少 stale 列表")

    items = []
    for row in rep["stale"]:
        row = row or {}
        items.append({
            "table": row.get("table"),
            "source": row.get("source"),
            "freq": row.get("freq"),
            "freshness": row.get("freshness"),
            "lag_days": row.get("lag_days"),
            "sla_days": row.get("sla_days"),
            "grade": row.get("grade"),
        })
    counts = Counter(str(it.get("grade") or "") for it in items)
    by_grade = {g: int(counts.get(g, 0)) for g in GRADES}
    scale = rep.get("scale") if isinstance(rep.get("scale"), dict) else {}
    return {
        "source": "tools/data_hub/govern.py::audit()",
        "today": rep.get("today"),
        "scope": "stale_only",
        "by_grade": by_grade,
        "grade_counts": dict(by_grade),
        "scale": {
            "sources": scale.get("sources"),
            "catalog_tables": scale.get("catalog_tables"),
            "physical_tables": scale.get("physical_tables"),
            "undated_tables": scale.get("undated_tables"),
        },
        "skipped_by_lifecycle": len(rep.get("skipped_by_lifecycle") or []),
        "note": SLA_NOTE,
        "batch_profile_note": BATCH_NOTE,
        "items": items,
    }


def _sla(handler, get):
    grade = _enum_param(get, "grade", GRADES)
    limit, offset = _range(get)
    data, gen, cached = _cached("sla", _sla_loader)
    items = data["items"]
    if grade is not None:
        items = [it for it in items if it.get("grade") == grade]
    body = {k: v for k, v in data.items() if k != "items"}
    body.update({
        "ok": True, "endpoint": EP_SLA, "generated_at": gen,
        "total": len(items), "returned": len(_page(items, limit, offset)),
        "items": _page(items, limit, offset), "cached": cached,
    })
    handler._json(body)


# ── 2. 上游健康度（最新快照）───────────────────────────────────
_UPSTREAM_ITEM_KEYS = (
    "table", "source", "freq", "verdict", "lag_days", "sla_days",
    "local", "remote_latest", "sampled", "present", "reason", "remote_rows",
)


def _upstream_loader():
    path = _latest_upstream_path()
    snap = _load_json(path)
    if not isinstance(snap, dict):
        raise _Unavailable("快照结构异常：顶层不是对象")
    rows = snap.get("rows")
    if not isinstance(rows, list) or not rows:
        raise _Unavailable("快照结构异常：rows 缺失或为空")

    items = []
    for row in rows:
        row = row or {}
        item = {k: row.get(k) for k in _UPSTREAM_ITEM_KEYS}
        item["state"] = VERDICT_STATE.get(str(item.get("verdict") or ""), "UNDECIDABLE")
        items.append(item)

    v_counts = Counter(str(it.get("verdict") or "") for it in items)
    by_verdict = {k: int(v) for k, v in v_counts.items()}
    s_counts = Counter(it["state"] for it in items)
    by_state = {s: int(s_counts.get(s, 0)) for s in STATES}
    summary = snap.get("summary") if isinstance(snap.get("summary"), dict) else {}
    per_source = {}
    for sid, info in (summary.get("per_source") or {}).items():
        if not isinstance(info, dict):
            continue
        counts = {k: int(v) for k, v in info.items()
                  if not str(k).startswith("_") and isinstance(v, int)}
        per_source[str(sid)] = {
            "name": info.get("_name"),
            "total": info.get("_total"),
            "by_verdict": counts,
        }
    return {
        "source": os.path.basename(path),
        "source_generated_at": snap.get("generated_at"),
        "snapshot_today": snap.get("today"),
        "probed": snap.get("probed"),
        "probe_hits": snap.get("probe_hits"),
        "errors": len(snap.get("errors") or []) if isinstance(snap.get("errors"), list)
                  else snap.get("errors"),
        "by_verdict": by_verdict,
        "by_state": by_state,
        "per_source": per_source,
        "total_exact_note": TOTAL_EXACT_NOTE,
        "items": items,
    }


def _upstream(handler, get):
    state = _enum_param(get, "state", STATES)
    verdict = _enum_param(get, "verdict", VERDICTS)
    limit, offset = _range(get)
    data, gen, cached = _cached("upstream", _upstream_loader)
    src = _enum_param(get, "source", tuple(sorted(data["per_source"])))
    items = data["items"]
    if state is not None:
        items = [it for it in items if it.get("state") == state]
    if verdict is not None:
        items = [it for it in items if it.get("verdict") == verdict]
    if src is not None:
        items = [it for it in items if it.get("source") == src]
    body = {k: v for k, v in data.items() if k != "items"}
    body.update({
        "ok": True, "endpoint": EP_UPSTREAM, "generated_at": gen,
        "total": len(items), "returned": len(_page(items, limit, offset)),
        "items": _page(items, limit, offset), "cached": cached,
    })
    handler._json(body)


# ── 3. catalog lifecycle ──────────────────────────────────────
def _lifecycle_loader():
    cat = _load_json(CATALOG_PATH)
    if not isinstance(cat, dict) or not isinstance(cat.get("tables"), dict):
        raise _Unavailable("catalog 结构异常：缺少 tables 字典")
    tables = cat["tables"]
    items = []
    for name, rec in tables.items():
        rec = rec or {}
        status = rec.get("lifecycle")
        if not status:
            continue
        items.append({
            "table": name,
            "name": name,
            "lifecycle": status,
            "lifecycle_reason": rec.get("lifecycle_reason"),
        })
    items.sort(key=lambda it: (str(it.get("lifecycle")), str(it.get("table"))))
    counts = Counter(str(it["lifecycle"]) for it in items)
    return {
        "source": "quant_backtest/data/catalog.json",
        "catalog_version": cat.get("version"),
        "catalog_tables": len(tables),
        "by_lifecycle": {k: int(v) for k, v in counts.items()},
        "by_status": {k: int(v) for k, v in counts.items()},
        "items": items,
    }


def _lifecycle(handler, get):
    limit, offset = _range(get)
    data, gen, cached = _cached("lifecycle", _lifecycle_loader)
    allowed = tuple(sorted(data["by_lifecycle"])) or LIFECYCLE_STATUS
    status = _enum_param(get, "status", allowed)
    items = data["items"]
    if status is not None:
        items = [it for it in items if it.get("lifecycle") == status]
    body = {k: v for k, v in data.items() if k != "items"}
    body.update({
        "ok": True, "endpoint": EP_LIFECYCLE, "generated_at": gen,
        "total": len(items), "returned": len(_page(items, limit, offset)),
        "items": _page(items, limit, offset), "cached": cached,
    })
    handler._json(body)


# ── 4. 源登记摘要 ─────────────────────────────────────────────
_SOURCE_KEYS = (
    "id", "name", "domain", "license", "freq", "proxy_policy",
    "status", "adapter", "covers", "linked_tables", "note",
)


def _sources_loader():
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    try:
        from tools.data_hub import registry  # noqa: PLC0415
        data = registry.load()
        summary = registry.summary(data)
        by_id = registry.by_id(data)
        problems = registry.validate(data)
    except Exception as exc:  # noqa: BLE001
        raise _Unavailable("%s: %s" % (type(exc).__name__, _scrub(exc)))
    if not isinstance(by_id, dict) or not by_id:
        raise _Unavailable("sources 结构异常：无登记源")

    items = []
    for sid, src in sorted(by_id.items()):
        src = src or {}
        item = {k: src.get(k) for k in _SOURCE_KEYS}
        item["id"] = src.get("id") or sid
        item["covers"] = list(src.get("covers") or [])
        item["linked_tables"] = list(src.get("linked_tables") or [])
        # 最小暴露：probe（外部 URL + headers）/ home / update_window 一律不进响应
        items.append(item)
    return {
        "source": "tools/data_hub/sources.yaml + registry.summary()",
        "total_exact_note": "total = sources.yaml 登记源数（registry.summary().total）。",
        "by_domain": summary.get("by_domain") or {},
        "by_status": summary.get("by_status") or {},
        "validate_problems": len(problems or []),
        "summary_total": summary.get("total"),
        "items": items,
    }


def _sources(handler, get):
    limit, offset = _range(get)
    data, gen, cached = _cached("sources", _sources_loader)
    domain = _enum_param(get, "domain", tuple(sorted(data["by_domain"])))
    status = _enum_param(get, "status", tuple(sorted(data["by_status"])))
    items = data["items"]
    if domain is not None:
        items = [it for it in items if it.get("domain") == domain]
    if status is not None:
        items = [it for it in items if it.get("status") == status]
    body = {k: v for k, v in data.items() if k != "items"}
    body.update({
        "ok": True, "endpoint": EP_SOURCES, "generated_at": gen,
        "total": len(items), "returned": len(_page(items, limit, offset)),
        "items": _page(items, limit, offset), "cached": cached,
    })
    handler._json(body)


# ── 入口（由 portal_api.do_GET 前缀早分支调用）────────────────
_ROUTES = {
    EP_SLA: _sla,
    EP_UPSTREAM: _upstream,
    EP_LIFECYCLE: _lifecycle,
    EP_SOURCES: _sources,
}


def _bad_param(handler, endpoint, message):
    handler._json({"ok": False, "error": "bad_param", "endpoint": endpoint,
                   "generated_at": _now_iso(), "message": message}, 400)


def _unavailable(handler, endpoint, detail):
    handler._json({"ok": False, "error": "governance_unavailable", "endpoint": endpoint,
                   "generated_at": _now_iso(), "detail": _scrub(detail)}, 503)


def handle_get(handler, path, qs=""):
    """只服务 4 个精确 GET 路径；其余 404。任何异常不冒泡成裸 500。"""
    endpoint = path if path in _ROUTES else (path or "")
    try:
        fn = _ROUTES.get(path)
        if fn is None:
            handler._json({"ok": False, "error": "not_found", "endpoint": endpoint,
                           "message": "未知 /api/data/governance 端点"}, 404)
            return
        params = parse_qs(qs or "")
        # 只回首个值并去空白；空串等同未传（白名单枚举/数字的取值校验在下层）
        get = lambda k, d=None: ((params.get(k) or [d])[0] or "").strip() or None  # noqa: E731
        fn(handler, get)
    except _BadParam as exc:
        _bad_param(handler, endpoint, str(exc))
    except _Unavailable as exc:
        _unavailable(handler, endpoint, str(exc))
    except Exception as exc:  # noqa: BLE001 — 最后兜底：503，不裸栈
        _unavailable(handler, endpoint, "%s: %s" % (type(exc).__name__, exc))
