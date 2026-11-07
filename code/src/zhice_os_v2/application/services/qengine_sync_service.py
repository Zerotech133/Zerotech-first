# -*- coding: utf-8 -*-
"""qengine_sync 只读查询服务。

数据源只允许是同步后的测试库（schema ``qengine_sync``），不读取 qengine
工作树，也不写生产库。连接串优先使用 ``QENGINE_SYNC_DB_URL``；local-dev/test
未配置时，从 ``DB_MAIN_URL`` 派生 ``zhice_qengine_sync_test`` 测试库。
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any

import psycopg2
from psycopg2 import pool as pg_pool
from psycopg2.extras import RealDictCursor
from sqlalchemy.engine import make_url

from zhice_os_v2.config.settings import get_settings

SCHEMA = "qengine_sync"
TEST_DB = "zhice_qengine_sync_test"

# qengine_sync 是只读旁路；小池即可覆盖页面并发，避免每个请求重新建连。
POOL_MIN = 1
POOL_MAX = 4
_pool: pg_pool.ThreadedConnectionPool | None = None
_pool_dsn: str | None = None
_pool_lock = threading.Lock()


class QengineSyncUnavailable(RuntimeError):
    """同步库未配置、不可连接或尚未完成回填。"""


def _dsn() -> str:
    settings = get_settings()
    if settings.qengine_sync_db_url.strip():
        return settings.qengine_sync_db_url.strip()
    if settings.app_env in ("local-dev", "test"):
        main = make_url(settings.db_main_url)
        return main.set(
            drivername="postgresql", database=TEST_DB
        ).render_as_string(hide_password=False)
    raise QengineSyncUnavailable(
        "QENGINE_SYNC_DB_URL 未配置；生产环境不会回退到主库"
    )


def _prepare_connection(conn) -> None:
    """池中物理连接创建时设置只读、自动提交和 search_path。"""
    conn.set_session(readonly=True, autocommit=True)
    with conn.cursor() as cur:
        cur.execute(f"SET search_path TO {SCHEMA}, public")


class _QengineConnectionPool(pg_pool.ThreadedConnectionPool):
    """连接在物理创建时完成只读配置，复用时不再多发 SET。"""

    def _connect(self):
        conn = super()._connect()
        try:
            _prepare_connection(conn)
        except Exception:
            conn.close()
            raise
        return conn


def _connect():
    """新建一条已配置的只读连接（保留给一次性调用/基准脚本）。"""
    try:
        conn = psycopg2.connect(_dsn())
        _prepare_connection(conn)
        return conn
    except Exception as exc:  # noqa: BLE001 - 统一转换为可读的 503
        raise QengineSyncUnavailable("qengine_sync 测试库不可连接") from exc


def _get_pool() -> pg_pool.ThreadedConnectionPool:
    """懒加载 1–4 条连接的只读线程安全池；DSN 变化时重建。"""
    global _pool, _pool_dsn
    dsn = _dsn()
    with _pool_lock:
        if _pool is not None and _pool_dsn == dsn:
            return _pool
        if _pool is not None:
            try:
                _pool.closeall()
            except Exception:  # noqa: BLE001 - 旧池已不可用
                pass
            _pool = None
            _pool_dsn = None
        try:
            _pool = _QengineConnectionPool(POOL_MIN, POOL_MAX, dsn)
        except Exception as exc:  # noqa: BLE001
            _pool = None
            _pool_dsn = None
            raise QengineSyncUnavailable("qengine_sync 测试库不可连接") from exc
        _pool_dsn = dsn
        return _pool


def close_pool() -> None:
    """关闭池内全部连接；测试和进程退出可调用，重复调用安全。"""
    global _pool, _pool_dsn
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.closeall()
            finally:
                _pool = None
                _pool_dsn = None


@contextmanager
def _connection():
    pool = _get_pool()
    conn = None
    try:
        conn = pool.getconn()
        yield conn
    except Exception:
        # 连接可能处于可恢复或坏状态；异常路径一律丢弃，避免污染池。
        if conn is not None:
            try:
                pool.putconn(conn, close=True)
            except Exception:  # noqa: BLE001
                pass
            conn = None
        raise
    finally:
        if conn is not None:
            try:
                pool.putconn(conn)
            except Exception:  # noqa: BLE001
                pass


def _rows(cur, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    cur.execute(sql, params)
    return [dict(row) for row in cur.fetchall()]


def _one(cur, sql: str, params: tuple = ()) -> dict[str, Any] | None:
    rows = _rows(cur, sql, params)
    return rows[0] if rows else None


def fetch_snapshot(detail_sid: str = "LX20") -> dict[str, Any]:
    """返回策略中台首版页面所需的完整只读快照。"""
    try:
        with _connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                run = _one(
                    cur,
                    (
                        "select * from qg_sync_run "
                        "order by finished_at desc limit 1"
                    ),
                )
                if not run:
                    raise QengineSyncUnavailable("qengine_sync 尚无同步批次")
                asof = str(run["asof"])
                latest = _one(cur, ("select * from qg_latest"))
                pool_day = _one(
                    cur,
                    ("select * from qg_pool_day order by asof desc limit 1"),
                )

                strategies = _rows(
                    cur,
                    (
                        """select sid,name,system,ekind,tiers,can_hold,doc,status,
                                  status_label,level,n_total,n_closed,n_open,n_days,
                                  win,avg,med,pl,worst,best,exc,exc_t,stp,hold,
                                  first_sig,last_sig,n_issues,enabled,enabled_from,
                                  enabled_why,grade,grade_why,g_n,g_n_day,g_t_trade,
                                  g_t_day,g_ci_lo,g_ci_hi,g_pct,g_p,g_q,g_pos_seg,
                                  g_seg,g_rnd,g_sub,g_nd_ok
                           from qg_strategy
                           order by
                             case when grade='A' then 1 when grade='B' then 2
                                  when grade='C' then 3 when grade='D' then 4
                                  else 5 end,
                             exc desc nulls last, sid"""
                    ),
                )
                pool = _rows(
                    cur, ("select sid,name from qg_pool_strategy order by sid")
                )
                pool_sids = {r["sid"] for r in pool}

                latest_picks = _rows(
                    cur,
                    (
                        """select signal_date,sid,ts_code,name,industry,
                                  seq as rank_in,gap_pct,stop_px,ref_px,score,
                                  vetoed,veto_why
                           from qg_pending
                           where signal_date=%s and vetoed=0
                           order by sid,seq nulls last"""
                    ),
                    (asof,),
                )
                # 复用同一批最新候选，不再分别查 pick_counts / top_picks。
                # 旧查询按 sid,seq nulls last 排序，dict 后写覆盖先写；这里保持同样语义。
                pick_counts: dict[str, int] = {}
                top_picks: dict[str, dict[str, Any]] = {}
                top_pick_keys = (
                    "sid",
                    "ts_code",
                    "name",
                    "rank_in",
                    "gap_pct",
                    "stop_px",
                    "ref_px",
                    "score",
                )
                for row in latest_picks:
                    sid = row["sid"]
                    pick_counts[sid] = pick_counts.get(sid, 0) + 1
                    top_picks[sid] = {
                        key: row.get(key) for key in top_pick_keys
                    }
                nav = _rows(cur, ("select * from qg_nav order by date"))
                days = _rows(cur, ("select * from qg_day order by signal_date"))
                issues = _rows(
                    cur,
                    (
                        """select issue_id,scope,code,level,sev,title,ev,act,
                                  first_date,last_date,hits,status
                           from qg_issue
                           order by sev desc, hits desc nulls last, code"""
                    ),
                )
                latest_day = days[-1] if days else {}

                # strategy/day/pool/issue/latest_picks 已在上方加载，直接取长度；
                # 其余 4 张表用一条标量子查询合并，避免 5 次单独 count。
                count_row = _one(
                    cur,
                    (
                        "select "
                        "(select count(*) from qg_pick) as pick, "
                        "(select count(*) from qg_trade) as trade, "
                        "(select count(*) from qg_pool_candidate) "
                        "as pool_candidate, "
                        "(select count(*) from qg_pending) as pending"
                    ),
                ) or {}
                counts = {
                    "strategy": len(strategies),
                    "pool_strategy": len(pool),
                    "day": len(days),
                    "pick": int(count_row.get("pick") or 0),
                    "trade": int(count_row.get("trade") or 0),
                    "pool_candidate": int(
                        count_row.get("pool_candidate") or 0
                    ),
                    "issue": len(issues),
                    "pending": int(count_row.get("pending") or 0),
                    "latest_picks": len(latest_picks),
                }
                grade_counts: dict[str, int] = {}
                for s in strategies:
                    key = s.get("grade") or "未评级"
                    grade_counts[key] = grade_counts.get(key, 0) + 1

                for s in strategies:
                    sid = s["sid"]
                    s["in_pool"] = sid in pool_sids
                    s["latest_pick_count"] = int(pick_counts.get(sid, 0))
                    s["latest_top_pick"] = top_picks.get(sid)

                return {
                    "meta": {
                        "title": "司马徽 · 策略中台",
                        "subtitle": "qengine 运行数据只读镜像",
                        "asof": asof,
                        "generated_at": run.get("finished_at"),
                        "source_commit": run.get("source_commit"),
                        "source_dirty": run.get("source_dirty"),
                        "sync_id": run.get("sync_id"),
                        "database": _database_label(),
                        "schema": SCHEMA,
                        "note": "qengine_sync 只读接口 · 非生产 · 不构成交易建议",
                        "latest_generated_at": (
                            latest.get("generated_at") if latest else None
                        ),
                    },
                    "counts": counts,
                    "grade_counts": grade_counts,
                    "lx_count": len(
                        [s for s in strategies if str(s["sid"]).startswith("LX")]
                    ),
                    "nav": nav,
                    "latest_nav": nav[-1] if nav else None,
                    "days": days[-5:],
                    "latest_day": latest_day,
                    "pool": pool,
                    "pool_day": pool_day,
                    "strategies": strategies,
                    "latest_picks": latest_picks,
                    "issues": issues,
                    "detail": fetch_strategy_detail(detail_sid, conn=conn),
                }
    except QengineSyncUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - 数据库结构缺失/查询错误统一 503
        raise QengineSyncUnavailable("qengine_sync 查询失败") from exc


def _database_label() -> str:
    try:
        return make_url(_dsn()).database or ""
    except Exception:  # noqa: BLE001
        return ""


def fetch_strategy_detail(
    sid: str, *, conn=None
) -> dict[str, Any]:
    """返回单个策略的详情；不存在时抛 KeyError。"""
    if conn is None:
        with _connection() as owned_conn:
            return fetch_strategy_detail(sid, conn=owned_conn)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            strategy = _one(
                cur,
                (
                    """select s.*,
                              exists(
                                  select 1 from qg_pool_strategy p
                                  where p.sid=s.sid
                              ) as in_pool
                       from qg_strategy s where s.sid=%s"""
                ),
                (sid,),
            )
            if not strategy:
                raise KeyError(sid)
            days = _rows(
                cur,
                (
                    """select signal_date,sid,n,closed,open,win,avg,exc,exc_t,
                              stp,hold,level,verdict
                       from qg_strategy_day where sid=%s order by signal_date"""
                ),
                (sid,),
            )
            picks = _rows(
                cur,
                (
                    """select signal_date,sid,ts_code,name,industry,rank_in,
                              gap_pct,stop_px,entry_px,shares,cost,status,
                              exit_date,exit_px,exit_reason,hold_days,
                              ret_net_pct,ret_pct,excess
                       from qg_pick where sid=%s
                       order by signal_date desc, rank_in nulls last limit 16"""
                ),
                (sid,),
            )
            trades = _rows(
                cur,
                (
                    """select track,signal_date,entry_date,sid,ts_code,name,
                              status,exit_date,exit_px,ret_net_pct,exit_reason,
                              hold_days
                       from qg_trade where sid=%s
                       order by signal_date desc limit 12"""
                ),
                (sid,),
            )
            extreme_rows = _rows(
                cur,
                (
                    """(select 'best' as kind,signal_date,ts_code,name,
                              status,ret_net_pct,excess,hold_days
                       from qg_pick where sid=%s and ret_net_pct is not null
                       order by ret_net_pct desc limit 1)
                       union all
                       (select 'worst' as kind,signal_date,ts_code,name,
                               status,ret_net_pct,excess,hold_days
                        from qg_pick where sid=%s and ret_net_pct is not null
                        order by ret_net_pct asc limit 1)"""
                ),
                (sid, sid),
            )
            extreme_by_kind = {
                row["kind"]: {
                    key: value
                    for key, value in row.items()
                    if key != "kind"
                }
                for row in extreme_rows
            }
            best = extreme_by_kind.get("best")
            worst = extreme_by_kind.get("worst")
            issues = _rows(
                cur,
                (
                    """select issue_id,scope,code,level,sev,title,ev,act,
                              first_date,last_date,hits,status
                       from qg_issue
                       where code=%s or scope=%s
                       order by sev desc, hits desc nulls last"""
                ),
                (sid, sid),
            )
            return {
                "sid": sid,
                "strategy": strategy,
                "days": days,
                "picks": picks,
                "trades": trades,
                "extremes": {"best": best, "worst": worst},
                "issues": issues,
            }
    except KeyError:
        raise
    except Exception as exc:  # noqa: BLE001 - 与 snapshot 一致的 503 错误出口
        raise QengineSyncUnavailable("qengine_sync 查询失败") from exc


def fetch_health() -> dict[str, Any]:
    """同步批次的只读健康摘要。"""
    try:
        with _connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                run = _one(
                    cur,
                    (
                        "select * from qg_sync_run "
                        "order by finished_at desc limit 1"
                    ),
                )
                checks = _rows(
                    cur,
                    (
                        "select * from qg_sync_check "
                        "order by check_id"
                    ),
                ) if _table_exists(cur, "qg_sync_check") else []
                return {"sync": run, "checks": checks}
    except QengineSyncUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise QengineSyncUnavailable("qengine_sync 健康查询失败") from exc


def _table_exists(cur, table: str) -> bool:
    cur.execute(
        "select 1 from information_schema.tables "
        "where table_schema=%s and table_name=%s",
        (SCHEMA, table),
    )
    return cur.fetchone() is not None


def fetch_pool_candidates(
    *, asof: str | None = None, sid: str | None = None,
    limit: int = 100, offset: int = 0,
) -> dict[str, Any]:
    """全量候选表的分页只读视图（用于后续候选/审计页）。"""
    try:
        with _connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                if not asof:
                    latest = _one(
                        cur,
                        ("select asof from qg_pool_day order by asof desc limit 1"),
                    )
                    asof = str(latest["asof"]) if latest else ""
                where = ["asof=%s"]
                params: list[Any] = [asof]
                if sid:
                    where.append("sid=%s")
                    params.append(sid)
                clause = " and ".join(where)
                total = _one(
                    cur,
                    (f"select count(*) n from qg_pool_candidate where {clause}"),
                    tuple(params),
                )["n"]
                rows = _rows(
                    cur,
                    (
                        f"""select row_no,asof,sid,code,name,industry,px,stop,
                                   gap,eff,score,ret,exc,hold,rs,complete
                            from qg_pool_candidate where {clause}
                            order by sid,score desc nulls last,code
                            limit %s offset %s"""
                    ),
                    tuple(params + [limit, offset]),
                )
                return {"asof": asof, "total": total, "rows": rows}
    except QengineSyncUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise QengineSyncUnavailable("qengine_sync 候选查询失败") from exc
