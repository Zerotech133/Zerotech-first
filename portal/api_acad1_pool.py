# -*- coding: utf-8 -*-
"""ACAD1 官方推荐池只读数据口（IFS-POOL，WO-IFS-POOL-20261103）。

口径（2026-11-03 定稿）
-----------------------
`/api/acad1/pool` 的官方池读取优先级（**只读，不执行/不下单/不写库**）：

1. ``pg_official``：直连智策 PG ``127.0.0.1:5446 / zhice_os_v2 / public.v2_acad1_pool``
   （唯一真源；11-02 pool_date = 30 selected，配额 CN12/HK10/US6/CRYPTO2）。
   DSN 复用 ``jev_common.DB_DSN``（与 ``portal/api_flow.py`` 同一真源，只读会话）；
   可用 ``ZHICE_V2_DSN`` 或 ``ACAD1_POOL_PG_DISABLE=1`` 显式禁用。
2. ``projection``：无法直连时读只读 PG→JSON 投影
   （``tools/datawarehouse/acad1_pool_projection.py`` 产出，
   schema=``acad1_pool_projection/v1``，``source_pool="pg"``）。
3. ``shadow``：以上均不可用才回退旧 JSON 池（``agent_platform/data/recommendation_pool``
   + ``pool_review``）。**shadow 自建 latest.json 明确不是官方**：
   ``official=false``、``review_status="shadow_only"``、``degraded_reason`` 显式标注。

响应契约（``acad1_pool_api/v2``）
---------------------------------
在旧字段（ok/mode/trading_permitted/generated_at/date/pool_id/owner/data_date/
freshness_days/stale/summary/plan/items/dates）基础上新增：

- ``source_mode``：pg_official / projection / shadow
- ``pool_id``：官方池身份（PG: ``ACAD1-POOL-PG-<YYYYMMDD>``）
- ``pool_date``：业务池日（非数据日）
- ``data_date``：数据日（新鲜度判定基准）
- ``published_at``：发布/落库时刻（PG max(selected_at)）
- ``review_status``：pool_reviewed / pool_published / shadow_only / unavailable
- ``official``：仅 pg_official/projection 为 True；shadow 显式为 False
- ``source_pool``：pg / projection / shadow（数据来源标识）
- ``degraded_reason``：降级原因（正常为 None）

数据安全：全部只读；不写 pool/paper/ledger；不修改运行时数据；不启动/重启服务。
"""

from __future__ import annotations

import io
import json
import os
import sys
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

DEFAULT_POOL_DIR = os.path.join(ROOT, "agent_platform", "data", "recommendation_pool")
DEFAULT_REVIEW_DIR = os.path.join(ROOT, "agent_platform", "data", "pool_review")
DEFAULT_PROJECTION_PATH = os.path.join(
    ROOT, "agent_platform", "data", "projections", "acad1_pool_projection.json"
)
PROJECTION_ENV = "ACAD1_POOL_PROJECTION_PATH"
PG_DISABLE_ENV = "ACAD1_POOL_PG_DISABLE"

RESPONSE_SCHEMA = "acad1_pool_api/v2"
PROJECTION_SCHEMA = "acad1_pool_projection/v1"
SOURCE_MODES = ("pg_official", "projection", "shadow")
OFFICIAL_SOURCE_MODES = ("pg_official", "projection")
REVIEW_STATUSES = ("pool_reviewed", "pool_published", "shadow_only", "unavailable")
PG_TABLE = "public.v2_acad1_pool"
PG_REVIEW_TABLE = "public.v2_acad1_review"
PG_TIMEOUT_S = 3

_PG_SQL = (
    "SELECT id, pool_date, strategy_id, code, name, market, signal, score, "
    "selection_reason, selected_by, selected_at, status, data_date "
    "FROM %s" % PG_TABLE
)


# ── 基础工具 ──────────────────────────────────────────────────────────
def _read_json(path):
    try:
        with io.open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _iso(value):
    """date/datetime → ISO 字符串；其它原样返回（只读投影需要 JSON 友好）。"""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _freshness_days(data_date, pool_date):
    try:
        d = date.fromisoformat(str(data_date)[:10])
        p = date.fromisoformat(str(pool_date)[:10])
        return max(0, (p - d).days)
    except (TypeError, ValueError):
        return None


def projection_path(path=None):
    """投影文件路径：显式参数 > 环境变量 > 默认 runtime 投影路径。"""
    return path or os.environ.get(PROJECTION_ENV) or DEFAULT_PROJECTION_PATH


# ── 投影契约校验（与 tools/datawarehouse/acad1_pool_projection.py 同口径） ──
def validate_projection(doc):
    """校验只读投影文档，返回错误列表（空 = 合法）。"""
    errors = []
    if not isinstance(doc, dict):
        return ["投影顶层必须为 dict"]
    if doc.get("schema") != PROJECTION_SCHEMA:
        errors.append("schema 必须为 %s" % PROJECTION_SCHEMA)
    if doc.get("source_pool") != "pg":
        errors.append("source_pool 必须为 'pg'（投影只允许来自 PG 只读查询）")
    rows = doc.get("rows")
    if not isinstance(rows, list):
        errors.append("rows 必须为 list")
    elif not rows:
        errors.append("rows 不得为空")
    if not isinstance(doc.get("pool_date"), str) or not doc.get("pool_date"):
        errors.append("pool_date 缺失")
    if not isinstance(doc.get("data_date"), str) or not doc.get("data_date"):
        errors.append("data_date 缺失")
    if not isinstance(doc.get("pool_id"), str) or not doc.get("pool_id"):
        errors.append("pool_id 缺失")
    if not isinstance(doc.get("published_at"), str) or not doc.get("published_at"):
        errors.append("published_at 缺失")
    if doc.get("review_status") not in REVIEW_STATUSES:
        errors.append(
            "review_status 取值非法: %r（允许: %s）"
            % (doc.get("review_status"), ", ".join(REVIEW_STATUSES))
        )
    if isinstance(rows, list) and "count" in doc and doc.get("count") != len(rows):
        errors.append("count 与 rows 长度不一致")
    return errors


def load_projection(path=None):
    """读取并校验投影文件。返回 (doc|None, 错误列表)。"""
    target = projection_path(path)
    doc = _read_json(target)
    if doc is None:
        return None, ["projection_missing:%s" % target]
    errors = validate_projection(doc)
    if errors:
        return None, ["projection_invalid:%s" % "; ".join(errors)]
    return doc, []


# ── PG 直连（只读；DSN 单一真源 = jev_common，其次 ZHICE_V2_DSN） ─────
def _pg_connect_kwargs():
    """返回 psycopg2 连接参数；不可用返回 (None, reason)。"""
    try:
        jev_dir = os.path.join(ROOT, "jev-eval")
        if jev_dir not in sys.path:
            sys.path.insert(0, jev_dir)
        import jev_common  # noqa: WPS433 — DSN 唯一真源（同 portal/api_flow.py）

        dsn = jev_common.DB_DSN
        return {
            "host": dsn["host"],
            "port": int(dsn["port"]),
            "user": dsn["user"],
            "password": dsn["password"],
            "dbname": dsn["dbname"],
            "connect_timeout": PG_TIMEOUT_S,
        }, None
    except Exception as exc:  # noqa: BLE001 — 缺 jev_common 时回落 env DSN（fail-visible 由调用方记录）
        dsn_url = os.environ.get("ZHICE_V2_DSN", "").strip()
        if dsn_url:
            return {"dsn": dsn_url, "connect_timeout": PG_TIMEOUT_S}, None
        return None, "pg_dsn_unavailable:%s" % str(exc)[:120]


def default_pg_reader(day=None):
    """真实只读 PG 查询（返回 {rows, dates, pool_date, published_at, review_verdict}）。"""
    kwargs, reason = _pg_connect_kwargs()
    if not kwargs:
        raise RuntimeError(reason)
    import psycopg2  # 产线既有依赖（Python311 已装）
    from psycopg2.extras import RealDictCursor

    conn = psycopg2.connect(**kwargs)
    try:
        try:
            conn.set_session(readonly=True)
        except Exception:  # noqa: BLE001 — 只读会话不可设时不阻断（SQL 本身只读）
            pass
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if day:
                cur.execute(_PG_SQL + " WHERE pool_date = %s ORDER BY market, code", (day,))
            else:
                cur.execute(
                    _PG_SQL
                    + " WHERE pool_date = (SELECT max(pool_date) FROM %s)"
                    " ORDER BY market, code" % PG_TABLE
                )
            rows = [dict(item) for item in cur.fetchall()]
            cur.execute(
                "SELECT DISTINCT pool_date FROM %s ORDER BY pool_date DESC LIMIT 14"
                % PG_TABLE
            )
            dates = [str(item["pool_date"]) for item in cur.fetchall()]
            pool_date = str(rows[0]["pool_date"]) if rows else day
            review_verdict = None
            if pool_date:
                cur.execute(
                    "SELECT verdict FROM %s WHERE review_date = %%s"
                    " ORDER BY id DESC LIMIT 1" % PG_REVIEW_TABLE,
                    (pool_date,),
                )
                got = cur.fetchone()
                review_verdict = str(got["verdict"]) if got else None
            published_at = ""
            if rows:
                stamps = [item.get("selected_at") for item in rows if item.get("selected_at")]
                if stamps:
                    published_at = _iso(max(stamps))
            return {
                "rows": rows,
                "dates": dates,
                "pool_date": pool_date,
                "published_at": published_at or "",
                "review_verdict": review_verdict,
            }
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def fetch_pg_pool(day=None, pg_reader=None):
    """PG 官方池读取（默认直连；测试可注入 pg_reader）。返回 (payload|None, reason)。"""
    if str(os.environ.get(PG_DISABLE_ENV, "")).strip() == "1":
        return None, "pg_disabled_by_env"
    try:
        reader = pg_reader or default_pg_reader
        payload = reader(day)
    except Exception as exc:  # noqa: BLE001 — 降级由上层统一处理（不假绿：reason 透传）
        return None, "pg_unavailable:%s" % str(exc)[:200]
    if not isinstance(payload, dict) or not payload.get("rows"):
        return None, "pg_no_rows"
    return payload, None


# ── 组装响应（旧字段兼容 + 新字段） ──────────────────────────────────
def _legacy_items(pool, review):
    """旧 JSON 池 + 复盘记录 → items（保持 2026-10-30 页面契约）。"""
    recs = {}
    for record in review.get("records") or []:
        recs[str(record.get("code") or "")] = record
    items = []
    for raw in pool.get("items") or []:
        code = str(raw.get("code") or "")
        rec = recs.get(code) or {}
        items.append(
            {
                "code": code,
                "name": raw.get("name") or rec.get("name") or "",
                "industry": raw.get("industry") or "",
                "tier": raw.get("tier") or "",
                "sources": raw.get("sources") or [],
                "data_date": raw.get("data_date") or "",
                "freshness_days": raw.get("freshness_days"),
                "expiry_date": raw.get("expiry_date") or "",
                "risk_flags": raw.get("risk_flags") or [],
                "recommendation": rec.get("recommendation"),
                "confidence": rec.get("confidence"),
                "price": rec.get("price"),
                "decision_status": rec.get("decision_status"),
                "action": rec.get("action") or "pending_analysis",
                "analyses": rec.get("analyses") or {},
                "node_exec": rec.get("node_exec") or [],
            }
        )
    return items


def _new_items(rows, review, review_status):
    """PG/投影行 + 复盘记录 → items（新增字段向后兼容旧页面）。"""
    recs = {}
    for record in review.get("records") or []:
        recs[str(record.get("code") or "")] = record
    items = []
    for raw in rows:
        code = str(raw.get("code") or "")
        rec = recs.get(code) or {}
        items.append(
            {
                "code": code,
                "name": raw.get("name") or "",
                "industry": raw.get("industry") or "",
                "tier": raw.get("tier") or "",
                "sources": [str(raw.get("strategy_id"))] if raw.get("strategy_id") else [],
                "data_date": str(raw.get("data_date") or ""),
                "freshness_days": raw.get("freshness_days"),
                "expiry_date": raw.get("expiry_date") or "",
                "risk_flags": raw.get("risk_flags") or [],
                "recommendation": rec.get("recommendation"),
                "confidence": rec.get("confidence"),
                "price": rec.get("price"),
                "decision_status": rec.get("decision_status"),
                "action": rec.get("action") or "pending_analysis",
                "analyses": rec.get("analyses") or {},
                "node_exec": rec.get("node_exec") or [],
                # 官方池来源字段（旧页面忽略；D1/审计可消费）
                "market": raw.get("market"),
                "strategy_id": raw.get("strategy_id"),
                "signal": raw.get("signal"),
                "score": float(raw["score"]) if raw.get("score") is not None else None,
                "selection_reason": raw.get("selection_reason") or "",
                "selected_by": raw.get("selected_by"),
                "selected_at": _iso(raw.get("selected_at")),
                "pool_date": str(raw.get("pool_date") or ""),
                "status": raw.get("status"),
            }
        )
    return items


def _summary(items, review):
    by_action = {key: 0 for key in ("buy", "watch", "avoid", "pending_analysis")}
    by_market = {}
    reviewed = 0
    for item in items:
        action = str(item.get("action") or "pending_analysis")
        by_action[action] = by_action.get(action, 0) + 1
        market = str(item.get("market") or "NA")
        by_market[market] = by_market.get(market, 0) + 1
        if item.get("decision_status") == "done":
            reviewed += 1
    summary = review.get("summary") if isinstance(review.get("summary"), dict) else None
    if summary:
        return summary
    return {
        "pool_total": len(items),
        "reviewed": reviewed,
        "pending": len(items) - reviewed,
        "by_action": by_action,
        "by_market": by_market,
    }


def _plan(rows, review, source_mode):
    legacy_plan = review.get("plan") if isinstance(review.get("plan"), dict) else {}
    if source_mode in OFFICIAL_SOURCE_MODES:
        selected = sum(1 for row in rows if str(row.get("status") or "") == "selected")
        return {
            "selected": legacy_plan.get("selected") if legacy_plan.get("selected") is not None
            else selected,
            "watch": len(legacy_plan.get("watch") or []) if legacy_plan else 0,
            "avoid": len(legacy_plan.get("avoid") or []) if legacy_plan else 0,
            "total_weight": legacy_plan.get("total_weight") if legacy_plan else None,
        }
    return {
        "selected": len(legacy_plan.get("selected") or []),
        "watch": len(legacy_plan.get("watch") or []),
        "avoid": len(legacy_plan.get("avoid") or []),
        "total_weight": legacy_plan.get("total_weight"),
    }


def _response_base(source_mode, pool_id, pool_date, data_date, published_at,
                   review_status, official, source_pool, degraded_reason):
    return {
        "ok": True,
        "schema": RESPONSE_SCHEMA,
        "mode": "acad1_pool_review",
        "trading_permitted": False,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_mode": source_mode,
        "official": official,
        "source_pool": source_pool,
        "pool_id": pool_id,
        "pool_date": pool_date,
        "data_date": data_date,
        "published_at": published_at,
        "review_status": review_status,
        "degraded_reason": degraded_reason,
    }


def build_pg_response(payload, review):
    """PG oofficial 官方池 → 兼容响应。"""
    rows = payload.get("rows") or []
    pool_date = str(payload.get("pool_date") or "")
    data_dates = [str(row.get("data_date") or "") for row in rows if row.get("data_date")]
    data_date = max(data_dates) if data_dates else pool_date
    review_status = "pool_reviewed" if payload.get("review_verdict") else "pool_published"
    items = _new_items(rows, review, review_status)
    resp = _response_base(
        "pg_official",
        "ACAD1-POOL-PG-%s" % str(pool_date).replace("-", ""),
        pool_date,
        data_date,
        str(payload.get("published_at") or ""),
        review_status,
        True,
        "pg",
        None,
    )
    resp.update(
        {
            "date": pool_date,
            "owner": "ACAD1",
            "freshness_days": _freshness_days(data_date, pool_date),
            "stale": (_freshness_days(data_date, pool_date) or 0) > 1,
            "summary": _summary(items, review),
            "plan": _plan(rows, review, "pg_official"),
            "items": items,
            "dates": list(payload.get("dates") or [pool_date]),
            "review_verdict": payload.get("review_verdict"),
            "pg_table": PG_TABLE,
        }
    )
    return resp


def build_projection_response(projection, review):
    """只读 PG→JSON 投影 → 兼容响应（source_mode=projection）。"""
    rows = list(projection.get("rows") or [])
    pool_date = str(projection.get("pool_date") or "")
    data_date = str(projection.get("data_date") or pool_date)
    review_status = str(projection.get("review_status") or "pool_published")
    if review_status not in REVIEW_STATUSES:
        review_status = "pool_published"
    items = _new_items(rows, review, review_status)
    resp = _response_base(
        "projection",
        str(projection.get("pool_id") or "ACAD1-POOL-PROJ-%s" % pool_date.replace("-", "")),
        pool_date,
        data_date,
        str(projection.get("published_at") or ""),
        review_status,
        True,
        "projection",
        None,
    )
    resp.update(
        {
            "date": pool_date,
            "owner": "ACAD1",
            "freshness_days": _freshness_days(data_date, pool_date),
            "stale": bool(projection.get("stale")),
            "summary": _summary(items, review),
            "plan": _plan(rows, review, "projection"),
            "items": items,
            "dates": list(projection.get("dates") or [pool_date]),
            "projection_generated_at": projection.get("generated_at"),
            "projection_source_table": projection.get("source_table"),
        }
    )
    return resp


def build_shadow_response(legacy, degraded_reason):
    """shadow 旧 JSON 池 → 响应（official=False，明确不是官方真源）。"""
    items = list(legacy.get("items") or [])
    day = str(legacy.get("date") or "")
    pool_id = legacy.get("pool_id") or ("ACAD1-SHADOW-%s" % day.replace("-", ""))
    resp = _response_base(
        "shadow",
        str(pool_id),
        day,
        str(legacy.get("data_date") or ""),
        None,
        "shadow_only",
        False,
        "shadow",
        degraded_reason,
    )
    resp.update(
        {
            "date": legacy.get("date") or "",
            "owner": legacy.get("owner") or "ACAD1",
            "freshness_days": legacy.get("freshness_days"),
            "stale": bool(legacy.get("stale")),
            "summary": legacy.get("summary") or _summary(items, {}),
            "plan": legacy.get("plan")
            or {"selected": 0, "watch": 0, "avoid": 0, "total_weight": None},
            "items": items,
            "dates": legacy.get("dates") or [],
        }
    )
    return resp


# ── 旧 JSON 池读取（shadow 兜底；保持 2026-10-30 旧契约） ─────────────
def read_legacy_snapshot(day=None, pool_dir=None, review_dir=None):
    """读旧 recommendation_pool / pool_review JSON（只读；shadow 兜底）。"""
    pool_dir = pool_dir or DEFAULT_POOL_DIR
    review_dir = review_dir or DEFAULT_REVIEW_DIR

    def available_dates():
        try:
            out = [
                name[:-5]
                for name in os.listdir(pool_dir)
                if name.startswith("20") and name.endswith(".json")
            ]
            return sorted(out, reverse=True)[:14]
        except OSError:
            return []

    dates = available_dates()
    day = day or (dates[0] if dates else "")
    pool_path = os.path.join(pool_dir, day + ".json") if day else ""
    pool = _read_json(pool_path) or {}
    review_path = os.path.join(review_dir, day + ".json") if day else ""
    review = _read_json(review_path) or {}
    items = _legacy_items(pool, review)
    summary = review.get("summary") or {
        "pool_total": len(items),
        "reviewed": sum(1 for item in items if item["decision_status"] == "done"),
        "pending": sum(1 for item in items if item["decision_status"] != "done"),
        "by_action": {
            action: sum(1 for item in items if item["action"] == action)
            for action in ("buy", "watch", "avoid", "pending_analysis")
        },
    }
    plan = review.get("plan") or {}
    return {
        "ok": True,
        "mode": "acad1_pool_review",
        "trading_permitted": False,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "date": day,
        "pool_id": pool.get("pool_id"),
        "owner": pool.get("owner") or "ACAD1",
        "data_date": pool.get("data_date"),
        "freshness_days": pool.get("freshness_days"),
        "stale": bool(pool.get("stale")),
        "summary": summary,
        "plan": {
            "selected": len(plan.get("selected") or []),
            "watch": len(plan.get("watch") or []),
            "avoid": len(plan.get("avoid") or []),
            "total_weight": plan.get("total_weight"),
        },
        "items": items,
        "dates": dates,
    }


# ── 主入口（只读；供 pool_data / 自检 CLI 调用） ─────────────────────
def load_pool_snapshot(
    day=None,
    legacy_loader=None,
    pg_reader=None,
    projection_file=None,
    allow_pg=True,
    allow_projection=True,
):
    """官方池优先（PG→投影→shadow 兜底）的只读快照。

    Parameters
    ----------
    day : str | None
        池日 YYYY-MM-DD；None=最新。
    legacy_loader : callable(day) -> dict | None
        旧 JSON 兜底读取器（pool_data 注入；缺省用本模块默认目录）。
    pg_reader : callable(day) -> dict | None
        PG 只读读取器（测试注入；缺省直连）。
    projection_file : str | None
        投影文件路径（测试注入；缺省 env/默认 runtime 路径）。
    allow_pg / allow_projection : bool
        显式关闭某一级来源（测试/降级演练用）。
    """
    reasons = []
    reader = pg_reader or default_pg_reader
    if allow_pg:
        payload, reason = fetch_pg_pool(day, pg_reader=reader)
        if payload:
            if day is None or str(payload.get("pool_date")) == str(day):
                return build_pg_response(payload, {})
            reasons.append("pg_has_no_rows_for_day:%s" % day)
        elif reason:
            reasons.append(reason)
    else:
        reasons.append("pg_disabled_by_caller")

    if allow_projection:
        doc, errors = load_projection(projection_file)
        if doc and (day is None or str(doc.get("pool_date")) == str(day)):
            return build_projection_response(doc, {})
        reasons.extend(errors or ["projection_has_no_rows_for_day:%s" % day])
    else:
        reasons.append("projection_disabled_by_caller")

    legacy = (legacy_loader or read_legacy_snapshot)(day)
    if isinstance(legacy, dict) and legacy.get("ok"):
        if day is None or not legacy.get("date") or str(legacy.get("date")) == str(day):
            return build_shadow_response(legacy, "; ".join(reasons) or "no_official_source")
        reasons.append("legacy_has_no_rows_for_day:%s" % day)
    return {
        "ok": False,
        "schema": RESPONSE_SCHEMA,
        "mode": "acad1_pool_review",
        "trading_permitted": False,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_mode": "shadow",
        "official": False,
        "source_pool": None,
        "pool_id": None,
        "pool_date": day or "",
        "data_date": "",
        "published_at": None,
        "review_status": "unavailable",
        "degraded_reason": "; ".join(reasons) or "no_source_available",
        "date": day or "",
        "owner": "ACAD1",
        "freshness_days": None,
        "stale": True,
        "summary": {"pool_total": 0, "reviewed": 0, "pending": 0,
                    "by_action": {"buy": 0, "watch": 0, "avoid": 0,
                                  "pending_analysis": 0}},
        "plan": {"selected": 0, "watch": 0, "avoid": 0, "total_weight": None},
        "items": [],
        "dates": [],
    }


def main(argv=None):
    """只读自检 CLI：打印当前官方池来源与池数。"""
    import argparse

    parser = argparse.ArgumentParser(description="ACAD1 官方推荐池只读自检")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--day", default="")
    parser.add_argument("--no-pg", action="store_true")
    args = parser.parse_args(argv)
    snap = load_pool_snapshot(day=args.day or None, allow_pg=not args.no_pg)
    if args.json:
        print(json.dumps(snap, ensure_ascii=False, indent=1, default=str))
        return 0
    print(
        "[acad1_pool] source_mode=%s official=%s review_status=%s"
        % (snap.get("source_mode"), snap.get("official"), snap.get("review_status"))
    )
    print(
        "[acad1_pool] pool_id=%s pool_date=%s data_date=%s published_at=%s"
        % (snap.get("pool_id"), snap.get("pool_date"),
           snap.get("data_date"), snap.get("published_at"))
    )
    print("[acad1_pool] count=%d degraded=%s"
          % (len(snap.get("items") or []), snap.get("degraded_reason")))
    return 0


__all__ = [
    "DEFAULT_POOL_DIR",
    "DEFAULT_PROJECTION_PATH",
    "DEFAULT_REVIEW_DIR",
    "OFFICIAL_SOURCE_MODES",
    "PROJECTION_ENV",
    "PROJECTION_SCHEMA",
    "PG_DISABLE_ENV",
    "PG_TABLE",
    "RESPONSE_SCHEMA",
    "REVIEW_STATUSES",
    "SOURCE_MODES",
    "build_pg_response",
    "build_projection_response",
    "build_shadow_response",
    "default_pg_reader",
    "fetch_pg_pool",
    "load_pool_snapshot",
    "load_projection",
    "main",
    "projection_path",
    "read_legacy_snapshot",
    "validate_projection",
]


if __name__ == "__main__":
    raise SystemExit(main())
