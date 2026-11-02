# -*- coding: utf-8 -*-
"""ACAD1 官方池只读 PG→JSON 投影（IFS-POOL，WO-IFS-POOL-20261103）。

背景
----
ACAD1 官方池唯一真源 = 智策 PG ``127.0.0.1:5446 / zhice_os_v2 / public.v2_acad1_pool``。
`/api/acad1/pool` 优先直连 PG；DP 不可直连时读取本脚本产出的只读投影
（schema=``acad1_pool_projection/v1``，``source_pool="pg"``）。**shadow 自建
``recommendation_pool/latest.json`` 不再作为官方源**（/api/acad1/pool 只在
source_mode=shadow 降级时显式标注）。

安全边界
--------
- **只读 DB**：仅 SELECT（连接后 ``set_session(readonly=True)``），不写库/不改 DDL；
- **不写运行时 pool/paper 数据**：唯一写点是 ``--out`` 指定的投影文件（显式给出才写；
  原子写 tmp+os.replace），默认 stdout 输出不落盘；
- 不代签/不生成交易指令；投影文件是只读契约产物，不是 DATA1 正式卡。

用法（只读）::

    # 打印最新 pool_date 投影 JSON（不写盘）
    python -X utf8 tools/datawarehouse/acad1_pool_projection.py --json

    # 指定池日 + 写投影到 runtime 投影路径（ops 手工/受控任务执行）
    python -X utf8 tools/datawarehouse/acad1_pool_projection.py \\
        --pool-date 2026-11-02 --out agent_platform/data/projections/acad1_pool_projection.json

    # 校验已落盘投影契约
    python -X utf8 tools/datawarehouse/acad1_pool_projection.py --check <file>

DSN 链：``jev_common.DB_DSN``（单一真源，同 portal/api_flow.py）→ ``ZHICE_V2_DSN``；
均不可用 → rc=2 ``missing_connector``（fail-visible，不假绿）。退出码：0 ok / 1 投影契约非法 / 2 连接器/用法错误。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DEFAULT_OUT = os.path.join(
    REPO, "agent_platform", "data", "projections", "acad1_pool_projection.json"
)
PROJECTION_SCHEMA = "acad1_pool_projection/v1"
SOURCE_POOL = "pg"
PG_TABLE = "public.v2_acad1_pool"
PG_REVIEW_TABLE = "public.v2_acad1_review"
PG_TIMEOUT_S = 5
REVIEW_STATUSES = ("pool_reviewed", "pool_published", "shadow_only", "unavailable")

_POOL_SQL = (
    "SELECT id, pool_date, strategy_id, code, name, market, signal, score, "
    "selection_reason, selected_by, selected_at, status, data_date "
    "FROM %s " % PG_TABLE
)


# ── 只读 PG 连接（DSN 单一真源） ──────────────────────────────────────
def _connect_kwargs(dsn_env="ZHICE_V2_DSN"):
    """返回 (kwargs, reason)：jev_common.DB_DSN 优先，其次 dsn_env 指定的 DSN。"""
    try:
        jev_dir = os.path.join(REPO, "jev-eval")
        if jev_dir not in sys.path:
            sys.path.insert(0, jev_dir)
        import jev_common  # noqa: WPS433 — 只读 DSN 单一真源

        dsn = jev_common.DB_DSN
        return {
            "host": dsn["host"],
            "port": int(dsn["port"]),
            "user": dsn["user"],
            "password": dsn["password"],
            "dbname": dsn["dbname"],
            "connect_timeout": PG_TIMEOUT_S,
        }, None
    except Exception as exc:  # noqa: BLE001
        dsn_url = os.environ.get(dsn_env, "").strip()
        if dsn_url:
            return {"dsn": dsn_url, "connect_timeout": PG_TIMEOUT_S}, None
        return None, "missing_connector:%s" % str(exc)[:160]


def fetch_rows(pool_date=None, dsn_env="ZHICE_V2_DSN"):
    """只读查询 v2_acad1_pool（返回 dict；连接/查询失败抛 RuntimeError）。"""
    kwargs, reason = _connect_kwargs(dsn_env)
    if not kwargs:
        raise RuntimeError(reason)
    import psycopg2  # noqa: WPS433 — Python311 产线既有依赖
    from psycopg2.extras import RealDictCursor

    conn = psycopg2.connect(**kwargs)
    try:
        try:
            conn.set_session(readonly=True)
        except Exception:  # noqa: BLE001
            pass
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if pool_date:
                cur.execute(_POOL_SQL + "WHERE pool_date = %s ORDER BY market, code", (pool_date,))
            else:
                cur.execute(
                    _POOL_SQL
                    + "WHERE pool_date = (SELECT max(pool_date) FROM %s)"
                    " ORDER BY market, code" % PG_TABLE
                )
            rows = [dict(item) for item in cur.fetchall()]
            cur.execute(
                "SELECT DISTINCT pool_date FROM %s ORDER BY pool_date DESC LIMIT 14"
                % PG_TABLE
            )
            dates = [str(item["pool_date"]) for item in cur.fetchall()]
            review_verdict = None
            if rows:
                cur.execute(
                    "SELECT verdict FROM %s WHERE review_date = %%s"
                    " ORDER BY id DESC LIMIT 1" % PG_REVIEW_TABLE,
                    (str(rows[0]["pool_date"]),),
                )
                got = cur.fetchone()
                review_verdict = str(got["verdict"]) if got else None
            return {"rows": rows, "dates": dates, "review_verdict": review_verdict}
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


# ── 投影契约（纯函数，可离线测试） ───────────────────────────────────
def _json_value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _normalize_row(row):
    """PG 行 → JSON 友好投影行（字段与 ODCS 契约 10+1 对齐）。"""
    out = {
        "id": row.get("id"),
        "pool_date": str(_json_value(row.get("pool_date")) or ""),
        "strategy_id": row.get("strategy_id") or "",
        "code": row.get("code") or "",
        "name": row.get("name") or "",
        "market": row.get("market") or "",
        "signal": row.get("signal") or "",
        "score": float(row["score"]) if row.get("score") is not None else None,
        "selection_reason": row.get("selection_reason") or "",
        "selected_by": row.get("selected_by") or "",
        "selected_at": _json_value(row.get("selected_at")) or "",
        "status": row.get("status") or "",
        "data_date": str(_json_value(row.get("data_date")) or ""),
    }
    return out


def build_projection(rows, dates=None, review_verdict=None, generated_at=None):
    """由 PG 行构建只读投影文档（不写盘）。"""
    normalized = [_normalize_row(dict(row)) for row in rows]
    if not normalized:
        raise ValueError("no rows: v2_acad1_pool 无数据，拒绝产出空投影")
    pool_dates = sorted({row["pool_date"] for row in normalized if row["pool_date"]})
    if len(pool_dates) != 1:
        raise ValueError(
            "投影只允许单一 pool_date（收到 %d 个: %s）" % (len(pool_dates), pool_dates[:5])
        )
    pool_date = pool_dates[0]
    data_dates = [row["data_date"] for row in normalized if row["data_date"]]
    data_date = max(data_dates) if data_dates else pool_date
    stamps = [row["selected_at"] for row in normalized if row["selected_at"]]
    published_at = max(stamps) if stamps else ""
    counts = {}
    for row in normalized:
        counts[row["market"]] = counts.get(row["market"], 0) + 1
    try:
        lag = (date.fromisoformat(pool_date) - date.fromisoformat(data_date)).days
    except (TypeError, ValueError):
        lag = None
    return {
        "schema": PROJECTION_SCHEMA,
        "source_pool": SOURCE_POOL,
        "source_table": PG_TABLE,
        "source_mode": "projection",
        "readonly": True,
        "generated_at": generated_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pool_id": "ACAD1-POOL-PG-%s" % pool_date.replace("-", ""),
        "pool_date": pool_date,
        "data_date": data_date,
        "data_date_lag_days": lag,
        "published_at": published_at,
        "review_status": "pool_reviewed" if review_verdict else "pool_published",
        "review_verdict": review_verdict,
        "stale": bool(lag is not None and lag > 1),
        "count": len(normalized),
        "counts_by_market": counts,
        "dates": list(dates or [pool_date]),
        "rows": normalized,
        "attribution": (
            "read-only projection of %s (唯一真源=智策 PG)；"
            "非 shadow 自建池；不代签、不下单、不写库" % PG_TABLE
        ),
    }


def validate_projection(doc):
    """校验投影契约；返回错误列表（空 = 合法）。"""
    errors = []
    if not isinstance(doc, dict):
        return ["投影顶层必须为 dict"]
    if doc.get("schema") != PROJECTION_SCHEMA:
        errors.append("schema 必须为 %s" % PROJECTION_SCHEMA)
    if doc.get("source_pool") != SOURCE_POOL:
        errors.append("source_pool 必须为 'pg'")
    if doc.get("source_mode") != "projection":
        errors.append("source_mode 必须为 'projection'")
    if doc.get("readonly") is not True:
        errors.append("readonly 必须为 true")
    rows = doc.get("rows")
    if not isinstance(rows, list) or not rows:
        errors.append("rows 必须为非空 list")
    elif doc.get("count") != len(rows):
        errors.append("count 与 rows 长度不一致")
    for key in ("pool_id", "pool_date", "data_date", "published_at", "generated_at"):
        if not isinstance(doc.get(key), str) or not doc.get(key):
            errors.append("%s 必须为非空字符串" % key)
    if doc.get("review_status") not in REVIEW_STATUSES:
        errors.append("review_status 取值非法: %r" % (doc.get("review_status"),))
    if isinstance(rows, list):
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                errors.append("rows[%d] 必须为 dict" % index)
                continue
            for key in ("pool_date", "market", "code", "name", "data_date", "status"):
                if not isinstance(row.get(key), str) or not row.get(key):
                    errors.append("rows[%d].%s 必须为非空字符串" % (index, key))
    return errors


def write_projection(doc, out_path):
    """原子写投影文件（tmp + os.replace）；返回绝对路径。"""
    path = os.path.abspath(out_path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(doc, handle, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise
    return path


def load_projection_file(path):
    """只读加载投影文件（失败抛 ValueError）。"""
    try:
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ValueError("投影文件读取失败 %s: %s" % (path, exc)) from exc
    errors = validate_projection(doc)
    if errors:
        raise ValueError("投影契约非法: %s" % "；".join(errors))
    return doc


# ── CLI ───────────────────────────────────────────────────────────────
def main(argv=None):
    parser = argparse.ArgumentParser(
        description="ACAD1 官方池只读 PG→JSON 投影（v2_acad1_pool）"
    )
    parser.add_argument("--pool-date", default="", help="池日 YYYY-MM-DD（缺省最新）")
    parser.add_argument("--out", default="", help="投影写入路径（缺省不写盘，仅 stdout）")
    parser.add_argument("--json", action="store_true", help="输出 JSON（stdout）")
    parser.add_argument("--check", default="", help="校验已落盘投影文件（rc0/1）")
    parser.add_argument("--dsn-env", default="ZHICE_V2_DSN", help="DSN 环境变量名")
    args = parser.parse_args(argv)

    if args.check:
        try:
            doc = load_projection_file(args.check)
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
            return 1
        print(json.dumps({"ok": True, "schema": doc["schema"],
                          "pool_date": doc["pool_date"], "count": doc["count"]},
                         ensure_ascii=False))
        return 0

    try:
        fetched = fetch_rows(args.pool_date or None, dsn_env=args.dsn_env)
        doc = build_projection(
            fetched["rows"], fetched.get("dates"), fetched.get("review_verdict")
        )
    except RuntimeError as exc:  # 连接器不可用：fail-visible rc=2
        print(json.dumps({"ok": False, "error": str(exc)[:300],
                          "fail_visible": True}, ensure_ascii=False))
        return 2
    except ValueError as exc:  # 空数据/多池日：契约拒绝 rc=1
        print(json.dumps({"ok": False, "error": str(exc)[:300]}, ensure_ascii=False))
        return 1

    if args.out:
        path = write_projection(doc, args.out)
        print(
            json.dumps(
                {"ok": True, "out": path, "pool_date": doc["pool_date"],
                 "count": doc["count"], "source_pool": doc["source_pool"],
                 "review_status": doc["review_status"]},
                ensure_ascii=False,
            )
        )
        return 0
    print(json.dumps(doc, ensure_ascii=False, indent=1))
    return 0


__all__ = [
    "DEFAULT_OUT",
    "PG_REVIEW_TABLE",
    "PG_TABLE",
    "PROJECTION_SCHEMA",
    "REVIEW_STATUSES",
    "SOURCE_POOL",
    "build_projection",
    "fetch_rows",
    "load_projection_file",
    "main",
    "validate_projection",
    "write_projection",
]


if __name__ == "__main__":
    raise SystemExit(main())
