# -*- coding: utf-8 -*-
"""数据仓统一取数 HTTP 入口（WP-I3，独立 handler 文件）。

GET /api/dw/stats              → 数据集总量/登记/入口/分域（v1 字段不变）
                                  + stats_v2（pg / contract_artifacts / connector_status）
GET /api/dw/list?domain=&q=&registered=0|1&exposed=0|1&kind=&limit=  → 数据集列表
                                  kind=pg_table / contract_artifact / all / file（逗号分隔可多选）
GET /api/dw/get?id=<dataset_id> → 数据集元数据（pg_table 附 connector/physical/ref_aliases/status）
GET /api/dw/sample?id=&n=      → 数据集样本（只读；pg_table 走只读 PG，
                                  DSN 缺失/连接失败显式 connector_unavailable）
GET /api/dw/search?q=&kind=&limit= → 关键词检索（默认含 pg/契约产物，kind 可收窄）
GET/POST /api/dw/query         → 专用查询（S3-06 P1：sqlite/parquet 过滤+分页+投影+count）
                                  pg_table/contract_artifact 显式 409 not_supported
                                  （不拼接任意 SQL；pg 只读 sample 可用）
                                  参数：id（或 dataset）/where/order/since/columns/limit/offset/count
                                  错误码：400 参数/字段非法、403 越权、404 未登记、409 载体不支持、
                                          503 内部错/连接器不可用
                                  注：POST 分派入口 handle_post 已实现；portal_api.do_POST 接线属 L2 重启批次

DWH-105（2026-11-02）：响应显式带数据时点与空结果三态，避免"空结果被误读为无数据/无机会"。
- data_date：盘点数据时点（dataset_inventory 文件 mtime 的日期，YYYY-MM-DD）；
- state：ok / no_data（查询为空、样本无行、路径缺失）/ not_found（dataset_id 未登记）/ stale
  （data_date 距今天数 > STALE_DAYS）/ not_supported / connector_unavailable；
- 非 ok 时带 reason；错误体（4xx/5xx）也带 data_date + reason。

DWH-103 pg_table（2026-11-04 DW-API-PG / WO-DW-API-PG-20261104）：
- 只读：pg 表仅经 registry 白名单物理名 + 只读 SELECT LIMIT（registry 内核）；本 API 不拼 SQL、
  不接受任意 SQL 参数（sql/raw_sql/statement/query → 400）；
- DSN 不可用（missing_dsn/driver_missing/connector_unavailable）→ 503
  `error=connector_unavailable` + `connector_status`（fail-visible，不假绿）。

纪律：只读；路径白名单在 dataset_registry.ALLOWED_ROOTS；不写业务数据；
注意：新增路由需重启 8091 才生效（重启属 L2，需批准）。
"""
from __future__ import annotations

import datetime
import decimal
import json
import sys
import urllib.parse
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools" / "datawarehouse"))

from dataset_registry import (  # noqa: E402
    ConnectorUnavailable,
    ContractArtifactUnavailable,
    DatasetRegistry,
    QueryNotSupported,
    QueryParamError,
)

STALE_DAYS = 7  # 盘点时点超过该天数 → state=stale（可配，测试通过 monkeypatch 固定）
_REG = None


def _reg() -> DatasetRegistry:
    global _REG
    if _REG is None:
        _REG = DatasetRegistry()
    return _REG


def _jsonable(value):
    """把 PG 样本中的 date/datetime/time/Decimal 转成 JSON 可序列化值。"""
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _int(v, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def _bool(v):
    if v in (None, ""):
        return None
    return v in ("1", "true", "True", "yes")


CATALOG_PATH = REPO / "quant_backtest" / "data" / "catalog.json"
WAREHOUSE_REGISTRY_PATH = REPO / "quant_backtest" / "data" / "warehouse_registry.json"


def _catalog_alignment() -> dict:
    """把 catalog/registry 的当前口径显式挂到 /api/dw/stats，避免与 /api/dataops 的 306 表读数打架。"""
    out = {}
    try:
        cat = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        out["catalog_version"] = cat.get("version")
        out["catalog_updated"] = cat.get("updated")
        out["catalog_tables"] = len(cat.get("tables") or {})
    except (OSError, ValueError, TypeError):
        pass
    try:
        reg = json.loads(WAREHOUSE_REGISTRY_PATH.read_text(encoding="utf-8"))
        out["registry_physical"] = reg.get("physical_count")
        out["registry_display"] = reg.get("display_count")
        out["registry_catalog_sha256"] = reg.get("catalog_sha256")
    except (OSError, ValueError, TypeError):
        pass
    return out


def _data_date():
    """盘点数据时点：dataset_inventory 文件 mtime 的日期（YYYY-MM-DD）。

    注意：这是**盘点文件被写入的日期**，不是数据最新交易日。两者混用会把
    「盘点没重跑」误读成「数据断流」（R12 D3）。需要交易口径请看 freshness 的
    latest_trade_date / latest_trade_date_source。
    """
    try:
        return datetime.date.fromtimestamp(
            Path(str(_reg().inventory_path)).stat().st_mtime).isoformat()
    except (AttributeError, OSError, TypeError, ValueError):
        return None


_FRESHNESS_CACHE: dict = {"key": None, "val": None}


def _freshness() -> dict:
    """数据时点三元组：盘点日期 / 盘点年龄 / 盘点时看到的最新数据日期。

    三个字段各自带来源标注，消费方不得把 data_date 当交易日期用。
    """
    out = {
        "data_date": _data_date(),
        "data_date_source": "inventory_mtime",
        "inventory_age_days": None,
        "latest_trade_date": None,
        "latest_trade_date_source": "unavailable",
    }
    try:
        inv_path = Path(str(_reg().inventory_path))
        st = inv_path.stat()
    except (AttributeError, OSError, TypeError, ValueError):
        return out
    if out["data_date"]:
        try:
            out["inventory_age_days"] = (
                datetime.date.today()
                - datetime.date.fromisoformat(out["data_date"])).days
        except ValueError:
            pass
    key = (str(inv_path), st.st_mtime)
    if _FRESHNESS_CACHE.get("key") == key:
        cached = dict(_FRESHNESS_CACHE.get("val") or {})
        out["latest_trade_date"] = cached.get("latest_trade_date")
        out["latest_trade_date_source"] = cached.get("latest_trade_date_source",
                                                    "unavailable")
        return out
    latest, source = None, "unavailable"
    try:
        inv = json.loads(inv_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        inv = {}
    for d in inv.get("datasets") or []:
        path = str(d.get("path") or "").lower().replace("\\", "/")
        # 只认行情数据目录；代码目录/.git 的 mtime 不是交易日期
        if ".git" in path or not any(k in path for k in ("/tushare", "/tdx")):
            continue
        v = str(d.get("latest") or "")[:10]
        if len(v) == 10 and v[4] == "-" and v > (latest or ""):
            latest = v
    if latest:
        source = "inventory_max_latest（盘点时点口径，非实时）"
    out["latest_trade_date"] = latest
    out["latest_trade_date_source"] = source
    _FRESHNESS_CACHE["key"] = key
    _FRESHNESS_CACHE["val"] = {"latest_trade_date": latest,
                               "latest_trade_date_source": source}
    return out


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
    fresh = _freshness()
    if data_date is not None:
        fresh["data_date"] = data_date
    out = {"data_date": fresh.pop("data_date"), "state": state}
    out.update(fresh)  # data_date_source / inventory_age_days / latest_trade_date(+source)
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


def _empty_list_reason(q=None, domain=None, registered=None, exposed=None,
                       kind=None) -> str:
    bits = []
    if q:
        bits.append("q=%r" % q)
    if domain:
        bits.append("domain=%r" % domain)
    if kind:
        bits.append("kind=%r" % kind)
    if registered is not None:
        bits.append("registered=%d" % int(registered))
    if exposed is not None:
        bits.append("exposed=%d" % int(exposed))
    return "no_data: 无匹配数据集（%s）" % ("，".join(bits) or "无过滤条件")


# ── pg_table / kind 过滤（2026-11-04 DW-API-PG）──────────────────────
FILE_KIND_ALIASES = ("file", "files", "file_level")


def _parse_kinds(reg, get, default_raw: str = ""):
    """解析 kind/kinds 参数 → (registry.list 的 kinds, 原始参数串)。

    - 无参数：``(None, "")`` → 文件级（向后兼容 v1）；
    - ``all``：含文件级 + pg_table + contract_artifact + declared；
    - ``file``：展开为文件级 kind 集合（注册表 datasets 的实际 kind）；
    - ``pg_table`` / ``contract_artifact``：单 kind；逗号分隔可多选。
    """
    raw = (get("kind") or get("kinds") or default_raw or "").strip()
    if not raw:
        return None, ""
    if raw.lower() == "all":
        return "all", raw
    items = [x.strip() for x in raw.split(",") if x.strip()]
    file_kinds = sorted({str(d.get("kind")) for d in getattr(reg, "datasets", [])
                         if d.get("kind")})
    expanded = []
    for item in items:
        if item.lower() in FILE_KIND_ALIASES:
            expanded.extend(file_kinds)
        else:
            expanded.append(item)
    return sorted(set(expanded)), raw


def _pg_meta(d: dict, reg) -> dict:
    """pg_table 元数据块（connector/physical/ref_aliases/status/可用性）。"""
    return {
        "connector": d.get("connector"),
        "physical": d.get("physical"),
        "ref": d.get("ref"),
        "ref_aliases": list(d.get("ref_aliases") or []),
        "status": d.get("status"),
        "available": bool(d.get("available")),
        "registered": bool(d.get("registered")),
        "exposed": bool(d.get("exposed")),
        "row_estimate": d.get("row_estimate"),
        "relkind": d.get("relkind"),
        "connector_status": getattr(reg, "connector_status", ""),
        "connector_error": getattr(reg, "connector_error", ""),
        "read_only": True,
        "query_supported": False,   # P1/P2 显式不支持行级 query（不拼任意 SQL）
        "sample_supported": True,
    }


def _connector_fail(handler, dataset_id: str, exc) -> None:
    """pg 连接器不可用：显式 fail-visible（missing_dsn/driver_missing/connector_unavailable）。"""
    status = getattr(exc, "status", "connector_unavailable") or "connector_unavailable"
    reason = "connector_unavailable: %s" % str(exc)[:300]
    payload = {
        "ok": False,
        "error": "connector_unavailable",
        "dataset_id": dataset_id,
        "connector_status": status,
        "code": 503,
        **_envelope("connector_unavailable", reason),
    }
    handler._json(payload, 503)


def _artifact_fail(handler, dataset_id: str, exc) -> None:
    """contract_artifact 不可用（缺失/漂移）：显式 fail-visible。"""
    payload = {
        "ok": False,
        "error": "contract_artifact_unavailable",
        "dataset_id": dataset_id,
        "code": 503,
        **_envelope("connector_unavailable", "contract_artifact_unavailable: %s"
                    % str(exc)[:300]),
    }
    handler._json(payload, 503)


def _query_fail(handler, error: str, code: int, reason: str,
                detail: dict | None = None, state: str = "") -> None:
    payload, rc = _fail(error, code, reason=reason, state=state)
    if detail:
        payload["detail"] = detail
    payload["code"] = code
    handler._json(payload, rc)


def _query_not_supported(handler, dataset_id: str, exc) -> None:
    """载体不支持行级 query：pg/契约产物显式 not_supported（不拼任意 SQL）。"""
    detail = dict(getattr(exc, "detail", None) or {})
    carrier = str(detail.get("carrier") or "")
    payload, rc = _fail("载体不支持: %s" % str(exc)[:160], exc.code,
                        reason="not_supported: %s" % str(exc)[:200],
                        state="not_supported")
    payload["code"] = rc
    payload["dataset_id"] = dataset_id
    if detail:
        payload["detail"] = detail
    if carrier:
        payload["carrier"] = carrier
        payload["query_supported"] = False
        if carrier in ("pg_table", "contract_artifact"):
            payload["hint"] = ("%s 只读；不支持任意 SQL/行级 query（pg_table 可用 sample/get）"
                               % carrier)
    handler._json(payload, rc)


def _query_http(handler, get) -> None:
    """GET/POST /api/dw/query 共用处理：参数校验 + 错误码映射 + 三态 envelope。"""
    did = (get("id") or get("dataset") or "").strip()
    if not did:
        _query_fail(handler, "id 必填", 400, "missing_param: id 必填")
        return
    # 安全护栏：本入口只接受结构化 where/order/columns，不接受任意 SQL 文本
    for raw_key in ("sql", "raw_sql", "statement", "query"):
        if get(raw_key):
            _query_fail(
                handler, "不接受任意 SQL", 400,
                "unsupported: 不接受任意 SQL/参数 %r；只接受结构化 where/order/columns，"
                "pg_table 不支持行级 query" % raw_key)
            return
    try:
        obj = _reg().query(
            did,
            where=get("where") or None,
            order=get("order") or get("order_by") or None,
            limit=get("limit") or 100,
            offset=get("offset") or 0,
            since=get("since") or None,
            columns=get("columns") or None,
            count=_bool(get("count")) is True,
        )
    except KeyError:
        _query_fail(handler, "dataset 不存在", 404,
                    "not_found: dataset_id=%r 未登记或未通过盘点过滤" % did, state="not_found")
        return
    except QueryParamError as exc:
        _query_fail(handler, "参数非法: %s" % str(exc)[:160], exc.code,
                    "bad_param: %s" % str(exc)[:200], exc.detail)
        return
    except QueryNotSupported as exc:
        _query_not_supported(handler, did, exc)
        return
    except PermissionError as exc:
        _query_fail(handler, "路径越权: %s" % str(exc)[:120], 403,
                    "permission_denied: %s" % str(exc)[:160])
        return
    dd = _data_date()
    payload = dict(obj)
    payload["query_state"] = obj.get("state")
    env = _ok_envelope(dd)
    if obj.get("state") == "no_data":
        env = _envelope("no_data", obj.get("reason") or "no_data: 查询命中 0 行", dd)
        env["query_state"] = "no_data"
    payload.update(env)
    handler._json(payload)


def handle_post(handler, path: str, body) -> None:
    """POST /api/dw/query：body 支持 JSON 对象或 form-urlencoded；其余路径 404。

    接线状态（2026-11-02 起）：portal_api.do_POST 已分派 `/api/dw/` 并传入 body
    （带 1MB 上限）；本函数只做参数解析/校验/调用 registry.query，不直接读 socket。
    """
    if path != "/api/dw/query":
        _query_fail(handler, "not found", 404,
                    "not_found: 未知 POST 端点 %r" % path, state="not_found")
        return
    raw = body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else str(body or "")
    raw = raw.strip()
    data = None
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except ValueError as exc:
            _query_fail(handler, "body JSON 解析失败", 400,
                        "bad_param: %s" % str(exc)[:160])
            return
    params = {}
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, (list, dict)):
                params[k] = [json.dumps(v, ensure_ascii=False)]
            elif isinstance(v, bool):
                params[k] = ["1" if v else "0"]
            elif v is None:
                params[k] = [""]
            else:
                params[k] = [str(v)]
    elif raw:
        params = urllib.parse.parse_qs(raw)
    get = lambda k, d="": (params.get(k) or [d])[0].strip()  # noqa: E731
    _query_http(handler, get)


def handle_get(handler, path: str, qs: str) -> None:
    params = urllib.parse.parse_qs(qs or "")
    get = lambda k, d="": (params.get(k) or [d])[0].strip()  # noqa: E731
    try:
        if path == "/api/dw/stats":
            reg = _reg()
            # v1 字段（total/registered/exposed/by_domain/by_kind）语义与数值不变；
            # DATA1-ALIGN（711d97567）追加 catalog/registry/display 对齐字段，本单原样保留。
            stats = reg.stats()
            stats.update(_catalog_alignment())
            payload = {"ok": True, **_ok_envelope(_data_date()), **stats}
            try:
                v2 = reg.stats_v2()
            except Exception as exc:  # noqa: BLE001 — v2 附加失败不破坏 v1，但显式标注
                v2 = None
                payload["stats_v2_error"] = "%s: %s" % (type(exc).__name__, str(exc)[:160])
            if isinstance(v2, dict):
                payload["stats_version"] = "v2"
                payload["stats_v2"] = v2
                payload["connector_status"] = v2.get("connector_status")
                payload["pg"] = v2.get("pg")
                payload["contract_artifacts"] = v2.get("contract_artifacts")
            else:
                payload["stats_version"] = "v1"
                payload["stats_v2"] = None
            handler._json(payload)
            return
        if path == "/api/dw/list":
            domain = get("domain") or None
            q = get("q") or None
            registered = _bool(get("registered"))
            exposed = _bool(get("exposed"))
            reg = _reg()
            kinds, raw_kind = _parse_kinds(reg, get)
            rows = reg.list(domain=domain, q=q, registered=registered,
                            exposed=exposed, kinds=kinds,
                            limit=_int(get("limit"), 50, 1, 200))
            dd = _data_date()
            base = {"ok": True, "count": len(rows), "datasets": rows}
            if raw_kind:
                base["kind"] = raw_kind
            if not rows:
                handler._json({**base,
                               **_envelope("no_data",
                                           _empty_list_reason(q, domain, registered,
                                                              exposed, raw_kind or None), dd)})
                return
            handler._json({**base, **_ok_envelope(dd)})
            return
        if path == "/api/dw/search":
            q = get("q")
            if not q:
                handler._json(*_fail("q 必填", 400, reason="missing_param: q 必填"))
                return
            limit = _int(get("limit"), 20, 1, 200)
            reg = _reg()
            # 默认含 pg/契约产物（kind=all），kind 参数可收窄（file/pg_table/...）
            kinds, raw_kind = _parse_kinds(reg, get, default_raw="all")
            rows = reg.list(q=q, limit=limit, kinds=kinds)
            dd = _data_date()
            base = {"ok": True, "count": len(rows), "datasets": rows,
                    "kind": raw_kind or "all"}
            if not rows:
                handler._json({**base,
                               **_envelope("no_data",
                                           _empty_list_reason(q=q, kind=raw_kind or "all"),
                                           dd)})
                return
            handler._json({**base, **_ok_envelope(dd)})
            return
        if path == "/api/dw/get":
            did = get("id")
            if not did:
                handler._json(*_fail("id 必填", 400, reason="missing_param: id 必填"))
                return
            reg = _reg()
            d = reg.get(did)
            if d is None and hasattr(reg, "resolve_ref"):
                d = reg.resolve_ref(did)  # 兼容 dataset/pg/<table>、<table> 等别名
            dd = _data_date()
            if d is None:
                handler._json(*_fail("dataset 不存在", 404, state="not_found",
                                     reason="not_found: dataset_id=%r 未登记或未通过盘点过滤" % did))
                return
            payload = {"ok": True, "dataset": d, **_ok_envelope(dd)}
            if d.get("kind") == "pg_table":
                payload["pg"] = _pg_meta(d, reg)
            handler._json(payload)
            return
        if path == "/api/dw/sample":
            did = get("id")
            if not did:
                handler._json(*_fail("id 必填", 400, reason="missing_param: id 必填"))
                return
            try:
                obj = _reg().sample(did, n=_int(get("n"), 3, 1, 20))
            except ConnectorUnavailable as exc:
                # DSN 缺失/驱动缺失/连接失败：显式 503 connector_unavailable（不假绿）
                _connector_fail(handler, did, exc)
                return
            except ContractArtifactUnavailable as exc:
                _artifact_fail(handler, did, exc)
                return
            dd = _data_date()
            obj = _jsonable(obj)
            empty = _sample_empty_reason(obj)
            if empty:
                handler._json({"ok": True, **obj, **_envelope("no_data", empty, dd)})
                return
            handler._json({"ok": True, **obj, **_ok_envelope(dd)})
            return
        if path == "/api/dw/query":
            _query_http(handler, get)
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
