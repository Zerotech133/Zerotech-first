# -*- coding: utf-8 -*-
"""因子服务只读目录 + 受限计算入口（P1）。

GET  /api/factor/health
GET  /api/factor/catalog?family=&q=&limit=&offset=
GET  /api/factor/catalog/<name>
POST /api/factor/compute

设计约束：
- 目录来自进程内 factor_lib.FACTORS，不读 parquet、不启动子进程；
- 计算由 factor_worker.py 子进程完成，8091 主进程只做校验/超时/缓存；
- 只读：不写库、不写文件、不联网；
- 所有响应带 catalog_version，计算缓存键含 catalog_version + 数据新鲜度。
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import threading
import time
from collections import Counter, OrderedDict
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote

from api_common import STATION, log_exc

_HERE = Path(__file__).resolve().parent
_WORKER = _HERE / "factor_worker.py"
_DATA = Path(STATION) / "quant_backtest" / "data" / "tushare"

_CACHE_TTL = 300
_CACHE_MAX = 8
_CACHE_MAX_ROWS = 5000
_TIMEOUT = 120
_MAX_BODY = 64 * 1024
_MAX_FACTORS = 20
_MAX_CODES = 300
_MAX_DAYS = 366
_MAX_ROWS = 50000

_LIB = None
_LIB_LOCK = threading.Lock()
_CATALOG = None
_CATALOG_AT = 0.0
_CATALOG_TTL = 60
_FRESHNESS = None
_FRESHNESS_AT = 0.0
_FRESHNESS_TTL = 5
_CACHE: "OrderedDict[str, tuple[float, dict]]" = OrderedDict()
_CACHE_LOCK = threading.Lock()
_COMPUTE_SEM = threading.BoundedSemaphore(2)
_DATE_RE = re.compile(r"^\d{8}$")
_CODE_RE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")


def _load_lib():
    """延迟加载 factor_lib；目录接口首次调用约 1s，之后进程内复用。"""
    global _LIB
    if _LIB is not None:
        return _LIB
    with _LIB_LOCK:
        if _LIB is None:
            research = str(Path(STATION) / "tools" / "research")
            if research not in sys.path:
                sys.path.insert(0, research)
            import factor_lib  # noqa: F401
            _LIB = factor_lib
    return _LIB


def _catalog_version(lib) -> str:
    rows = []
    for name in sorted(lib.FACTORS):
        s = lib.FACTORS[name]
        rows.append({
            "name": s.name, "family": s.family, "kind": s.kind,
            "direction": int(s.direction), "description": s.description,
            "source": s.source, "requires": list(s.requires),
        })
    raw = json.dumps({
        "lib_version": getattr(lib, "LIB_VERSION", ""),
        "factors": rows,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _refresh_catalog() -> None:
    global _CATALOG, _CATALOG_AT
    now = time.time()
    if _CATALOG is not None and now - _CATALOG_AT < _CATALOG_TTL:
        return
    lib = _load_lib()
    ver = _catalog_version(lib)
    rows = []
    for name in sorted(lib.FACTORS):
        s = lib.FACTORS[name]
        rows.append({
            "name": s.name, "family": s.family, "kind": s.kind,
            "direction": int(s.direction), "description": s.description,
            "source": s.source, "requires": list(s.requires),
            "status": "active",
        })
    fam = Counter(r["family"] for r in rows)
    src = Counter(r["source"] or "(unspecified)" for r in rows)
    _CATALOG = {
        "catalog_version": ver,
        "factor_lib_version": getattr(lib, "LIB_VERSION", ""),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "total": len(rows),
        "families": dict(sorted(fam.items())),
        "sources": dict(sorted(src.items())),
        "factors": rows,
    }
    _CATALOG_AT = now


def _freshness() -> dict:
    global _FRESHNESS, _FRESHNESS_AT
    now = time.time()
    if _FRESHNESS is not None and now - _FRESHNESS_AT < _FRESHNESS_TTL:
        return _FRESHNESS
    out = {}
    for table in ("daily", "daily_basic", "adj_factor", "index_daily"):
        p = _DATA / table
        files = sorted(p.glob("d_*.parquet")) if p.is_dir() else []
        if files:
            last = files[-1]
            out[table] = {
                "latest": last.stem[2:],
                "mtime": last.stat().st_mtime,
                "parts": len(files),
            }
        else:
            out[table] = {"latest": None, "mtime": None, "parts": 0}
    _FRESHNESS = out
    _FRESHNESS_AT = now
    return out


def _cache_get(key: str):
    now = time.time()
    with _CACHE_LOCK:
        item = _CACHE.get(key)
        if not item:
            return None
        exp, value = item
        if exp < now:
            _CACHE.pop(key, None)
            return None
        _CACHE.move_to_end(key)
        return value


def _cache_put(key: str, value: dict) -> None:
    if len(value.get("rows", [])) > _CACHE_MAX_ROWS:
        return
    with _CACHE_LOCK:
        _CACHE[key] = (time.time() + _CACHE_TTL, value)
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)


def _json_body(h):
    try:
        n = int(h.headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        return None, "invalid Content-Length"
    if n < 0 or n > _MAX_BODY:
        return None, "body too large"
    raw = h.rfile.read(n) if n > 0 else b""
    try:
        obj = json.loads(raw.decode("utf-8") or "{}")
    except Exception:  # noqa: BLE001
        return None, "body 不是合法 JSON"
    if not isinstance(obj, dict):
        return None, "body 必须是 JSON object"
    return obj, ""


def _read_int(v, default: int, lo: int, hi: int) -> int:
    if v is None or v == "":
        return default
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ValueError("参数必须是整数")
    if n < lo or n > hi:
        raise ValueError("参数超出范围 [%d, %d]" % (lo, hi))
    return n


def _validate_compute(req: dict) -> dict:
    factors = req.get("factors")
    if not isinstance(factors, list) or not factors:
        raise ValueError("factors 必须是非空数组")
    names = [str(x).strip() for x in factors if str(x).strip()]
    if not names:
        raise ValueError("factors 不能为空")
    if len(names) > _MAX_FACTORS:
        raise ValueError("factors 最多 %d 个" % _MAX_FACTORS)
    if len(set(names)) != len(names):
        raise ValueError("factors 不允许重复")
    start, end = str(req.get("start", "")), str(req.get("end", ""))
    for label, s in (("start", start), ("end", end)):
        if not _DATE_RE.match(s):
            raise ValueError("%s 必须是 YYYYMMDD" % label)
        try:
            datetime.strptime(s, "%Y%m%d")
        except ValueError:
            raise ValueError("%s 不是合法日期" % label) from None
    if start > end:
        raise ValueError("start 不能晚于 end")
    days = (datetime.strptime(end, "%Y%m%d") - datetime.strptime(start, "%Y%m%d")).days
    if days > _MAX_DAYS:
        raise ValueError("日期区间最多 %d 天" % _MAX_DAYS)
    universe = str(req.get("universe", "tradable"))
    if universe not in ("tradable", "all"):
        raise ValueError("universe 只能是 tradable/all")
    raw_codes = req.get("codes", [])
    if raw_codes is None:
        raw_codes = []
    if not isinstance(raw_codes, list):
        raise ValueError("codes 必须是数组")
    codes = []
    for c in raw_codes:
        c = str(c).strip()
        if not _CODE_RE.match(c):
            raise ValueError("非法 ts_code: %s" % c)
        codes.append(c)
    if len(codes) > _MAX_CODES:
        raise ValueError("codes 最多 %d 个" % _MAX_CODES)
    limit = _read_int(req.get("limit"), 50000, 1, _MAX_ROWS)
    fmt = str(req.get("format", "long"))
    if fmt != "long":
        raise ValueError("P1 仅支持 format=long")
    return {
        "factors": names, "start": start, "end": end,
        "universe": universe, "codes": sorted(set(codes)),
        "limit": limit, "format": fmt,
    }


def _run_worker(payload: dict) -> dict:
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(_WORKER)],
        input=json.dumps(payload, ensure_ascii=False),
        text=True, capture_output=True, timeout=_TIMEOUT,
        cwd=str(Path(STATION)),
    )
    out = (proc.stdout or "").strip()
    if not out:
        err = (proc.stderr or "").strip().splitlines()
        raise RuntimeError("worker 无输出" + (": " + err[-1] if err else ""))
    try:
        data = json.loads(out)
    except Exception:  # noqa: BLE001
        raise RuntimeError("worker 输出不是 JSON") from None
    if proc.returncode != 0 or not data.get("ok"):
        raise ValueError(str(data.get("error") or "worker 计算失败"))
    return data


def _compute(req: dict) -> tuple[dict, int]:
    try:
        p = _validate_compute(req)
    except ValueError as exc:
        return {"ok": False, "code": "bad_request", "error": str(exc)}, 400
    lib = _load_lib()
    _refresh_catalog()
    unknown = [n for n in p["factors"] if n not in lib.FACTORS]
    if unknown:
        return {"ok": False, "code": "unknown_factor",
                "error": "未知因子: " + ",".join(unknown[:10])}, 400
    fresh = _freshness()
    cat = _CATALOG or {}
    cat_ver = cat.get("catalog_version") or _catalog_version(lib)
    key_src = json.dumps({
        "catalog_version": cat_ver,
        "daily": (fresh.get("daily") or {}).get("latest"),
        "request": p,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    key = hashlib.sha256(key_src.encode("utf-8")).hexdigest()
    cached = _cache_get(key)
    if cached is not None:
        hit = dict(cached)
        hit["cache_hit"] = True
        return {"ok": True, "data": hit}, 200
    if not _COMPUTE_SEM.acquire(blocking=False):
        return {"ok": False, "code": "busy",
                "error": "因子计算并发已满（最多 2 个），请稍后重试"}, 429
    try:
        out = _run_worker(p)
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": "timeout",
                "error": "计算超时（>%ds），请缩小因子/日期/股票范围" % _TIMEOUT}, 504
    except ValueError as exc:
        return {"ok": False, "code": "compute_error", "error": str(exc)}, 400
    except Exception as exc:  # noqa: BLE001
        log_exc("factor-api.compute")
        return {"ok": False, "code": "worker_error",
                "error": "worker 失败: %s" % type(exc).__name__}, 500
    finally:
        _COMPUTE_SEM.release()

    data = {
        "catalog_version": cat_ver,
        "factor_lib_version": out.get("factor_lib_version", getattr(lib, "LIB_VERSION", "")),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "data_freshness": fresh,
        "request": p,
        "rows": out.get("rows", []),
        "count": int(out.get("count", 0)),
        "truncated": bool(out.get("truncated", False)),
        "elapsed_ms": out.get("elapsed_ms"),
        "cache_hit": False,
    }
    _cache_put(key, data)
    return {"ok": True, "data": data}, 200


def handle_get(h, path: str, qs: str = "") -> None:
    try:
        _refresh_catalog()
        cat = _CATALOG or {}
        if path == "/api/factor/health":
            h._json({
                "ok": True,
                "service": "factor",
                "catalog_version": cat.get("catalog_version"),
                "factor_lib_version": cat.get("factor_lib_version"),
                "total": cat.get("total"),
                "families": cat.get("families", {}),
                "data_freshness": _freshness(),
                "worker": _WORKER.exists(),
                "cache": {"size": len(_CACHE), "ttl_s": _CACHE_TTL},
            })
            return
        if path == "/api/factor/catalog":
            q = parse_qs(qs or "")
            family = (q.get("family", [""])[0] or "").strip()
            text = (q.get("q", [""])[0] or "").strip().lower()
            try:
                limit = _read_int(q.get("limit", [None])[0], 500, 1, 500)
                offset = _read_int(q.get("offset", [None])[0], 0, 0, 100000)
            except ValueError as exc:
                h._json({"ok": False, "code": "bad_request", "error": str(exc)}, 400)
                return
            rows = cat.get("factors", [])
            if family:
                rows = [r for r in rows if r["family"] == family]
            if text:
                rows = [r for r in rows
                        if text in r["name"].lower()
                        or text in (r["description"] or "").lower()]
            total = len(rows)
            page = rows[offset:offset + limit]
            h._json({"ok": True, "data": {
                "catalog_version": cat.get("catalog_version"),
                "total": total, "offset": offset, "limit": limit,
                "families": cat.get("families", {}),
                "factors": page,
            }})
            return
        if path.startswith("/api/factor/catalog/"):
            name = unquote(path[len("/api/factor/catalog/"):])
            for r in cat.get("factors", []):
                if r["name"] == name:
                    h._json({"ok": True, "data": {
                        "catalog_version": cat.get("catalog_version"),
                        "factor": r,
                    }})
                    return
            h._json({"ok": False, "code": "not_found",
                     "error": "unknown factor: %s" % name}, 404)
            return
        h._json({"ok": False, "code": "not_found",
                 "error": "unknown factor path: %s" % path}, 404)
    except Exception as exc:  # noqa: BLE001
        log_exc("factor-api.get")
        h._json({"ok": False, "code": "factor_error",
                 "error": "%s: %s" % (type(exc).__name__, exc)}, 500)


def handle_post(h, path: str) -> None:
    if path != "/api/factor/compute":
        h._json({"ok": False, "code": "not_found",
                 "error": "unknown factor POST: %s" % path}, 404)
        return
    try:
        req, err = _json_body(h)
        if req is None:
            h._json({"ok": False, "code": "bad_request", "error": err}, 413)
            return
        out, code = _compute(req)
        h._json(out, code)
    except Exception as exc:  # noqa: BLE001
        log_exc("factor-api.post")
        h._json({"ok": False, "code": "factor_error",
                 "error": "%s: %s" % (type(exc).__name__, exc)}, 500)
