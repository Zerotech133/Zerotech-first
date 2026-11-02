"""策略工作台只读 API（sidecar / daily / observations / backtest）。

只读；不读写 v2_strategy 平台目录，sidecar 不会进入付费策略广场。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from zhice_os_v2.api.dependencies import get_current_user
from zhice_os_v2.domain.security.user import User
from zhice_os_v2.infrastructure.db.session import get_session

router = APIRouter(prefix="/strategy-workspace", tags=["strategy-workspace"],
                   dependencies=[Depends(get_current_user)])


def _one(sql: str, **params):
    session = get_session()
    try:
        row = session.execute(text(sql), params).mappings().first()
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "strategy.not_found"})
        return dict(row)
    finally:
        session.close()


def _many(sql: str, **params) -> list[dict[str, Any]]:
    session = get_session()
    try:
        return [dict(r) for r in session.execute(text(sql), params).mappings().all()]
    finally:
        session.close()


def _strategy_id(strategy_id: str) -> str:
    return (strategy_id or "").strip().lower()


def _require(strategy_id: str) -> str:
    sid = _strategy_id(strategy_id)
    row = _one(
        "SELECT strategy_id FROM v2_strategy_sidecar WHERE strategy_id = :sid "
        "UNION ALL SELECT strategy_id FROM v2_strategy WHERE strategy_id = :sid AND "
        "visibility = 'PUBLIC' AND publication_status = 'PUBLISHED' LIMIT 1",
        sid=sid,
    )
    return row["strategy_id"]


@router.get("/list")
def list_sidecars(_: User = Depends(get_current_user)):
    sidecars = _many(
        "SELECT strategy_id, display_name, status, owner, ops_support, source_type, "
        "health, data_date, note, updated_at, 'sidecar' AS source_kind "
        "FROM v2_strategy_sidecar ORDER BY strategy_id"
    )
    platforms = _many(
        "SELECT strategy_id, strategy_name AS display_name, "
        "lower(status) AS status, '平台策略广场' AS owner, '' AS ops_support, "
        "'platform' AS source_type, 'unknown' AS health, NULL::date AS data_date, "
        "'策略广场已入库；平台策略无每日选股/观察数据' AS note, updated_at, "
        "'platform' AS source_kind FROM v2_strategy "
        "WHERE visibility = 'PUBLIC' AND publication_status = 'PUBLISHED' "
        "ORDER BY strategy_id"
    )
    return {"items": sidecars + platforms}


@router.get("/{strategy_id}/profile")
def profile(strategy_id: str, _: User = Depends(get_current_user)):
    sid = _require(strategy_id)
    try:
        return _one(
            "SELECT strategy_id, display_name, status, owner, ops_support, source_type, "
            "health, data_date, note, updated_at FROM v2_strategy_sidecar WHERE strategy_id = :sid",
            sid=sid,
        )
    except HTTPException:
        return _one(
            "SELECT strategy_id, strategy_name AS display_name, lower(status) AS status, "
            "'平台策略广场' AS owner, '' AS ops_support, 'platform' AS source_type, "
            "'unknown' AS health, NULL::date AS data_date, "
            "'策略广场已入库；平台策略无每日选股/观察数据' AS note, updated_at "
            "FROM v2_strategy WHERE strategy_id = :sid AND visibility = 'PUBLIC' "
            "AND publication_status = 'PUBLISHED'",
            sid=sid,
        )


@router.get("/{strategy_id}/daily")
def daily(
    strategy_id: str,
    date_str: str | None = Query(default=None, alias="date"),
    market: str | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    _: User = Depends(get_current_user),
):
    sid = _require(strategy_id)
    params: dict[str, Any] = {"sid": sid, "limit": limit}
    where = ["strategy_id = :sid"]
    if date_str:
        where.append("trade_date = :trade_date")
        params["trade_date"] = date.fromisoformat(date_str)
    if market:
        where.append("upper(market) = :market")
        params["market"] = market.upper()
    rows = _many(
        "SELECT market, trade_date, data_date, code, name, signal, score, reason, "
        "snapshot_sha FROM v2_strategy_daily WHERE " + " AND ".join(where) +
        " ORDER BY trade_date DESC, score DESC NULLS LAST, code LIMIT :limit",
        **params,
    )
    latest = rows[0]["trade_date"] if rows else None
    return {"strategy_id": sid, "trade_date": latest, "rows": rows}


@router.get("/{strategy_id}/observations")
def observations(
    strategy_id: str,
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=2000),
    _: User = Depends(get_current_user),
):
    sid = _require(strategy_id)
    params: dict[str, Any] = {"sid": sid, "limit": limit}
    where = ["strategy_id = :sid"]
    if start:
        where.append("signal_date >= :start")
        params["start"] = date.fromisoformat(start)
    if end:
        where.append("signal_date <= :end")
        params["end"] = date.fromisoformat(end)
    rows = _many(
        "SELECT signal_date, code, name, signal, entry_date, ret_t1, ret_t5, ret_t10, "
        "ret_t20, hit, max_dd, status FROM v2_strategy_observation WHERE " +
        " AND ".join(where) + " ORDER BY signal_date DESC, code LIMIT :limit",
        **params,
    )
    return {"strategy_id": sid, "rows": rows}


@router.get("/{strategy_id}/backtest")
def backtest(
    strategy_id: str,
    window: str | None = None,
    _: User = Depends(get_current_user),
):
    sid = _require(strategy_id)
    params: dict[str, Any] = {"sid": sid}
    where = ["strategy_id = :sid"]
    if window:
        where.append('"window" = :window')
        params["window"] = window
    rows = _many(
        'SELECT "window", cost_model, metrics, equity_path, manifest_sha, created_at '
        "FROM v2_strategy_backtest WHERE " + " AND ".join(where) +
        " ORDER BY created_at DESC LIMIT 20",
        **params,
    )
    return {"strategy_id": sid, "rows": rows}
