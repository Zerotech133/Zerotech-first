# -*- coding: utf-8 -*-
"""策略中台（qengine_sync）只读 API。

只读 qengine_sync 同步库；不写库、不调用 qengine、不执行选股/建仓/批准。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from zhice_os_v2.api.dependencies import get_current_user
from zhice_os_v2.application.services import qengine_sync_service as svc

router = APIRouter(
    prefix="/strategy-center",
    tags=["strategy-center"],
    dependencies=[Depends(get_current_user)],
)


def _unavailable(exc: svc.QengineSyncUnavailable) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={"code": "qengine_sync_unavailable", "message": str(exc)},
    )


@router.get("/snapshot")
def snapshot():
    """策略中台首版三页所需的聚合快照（含 LX20 详情样例）。"""
    try:
        return svc.fetch_snapshot()
    except svc.QengineSyncUnavailable as exc:
        raise _unavailable(exc) from exc


@router.get("/strategies")
def strategies():
    """全部注册策略（评级、池状态、最新候选计数）。"""
    try:
        data = svc.fetch_snapshot()
        return {
            "meta": data["meta"],
            "grade_counts": data["grade_counts"],
            "strategies": data["strategies"],
        }
    except svc.QengineSyncUnavailable as exc:
        raise _unavailable(exc) from exc


@router.get("/strategies/{sid}")
def strategy_detail(sid: str):
    """单个策略详情：信号日历、最近候选、成交、极值、诊断。"""
    try:
        return svc.fetch_strategy_detail(sid)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="策略不存在") from exc
    except svc.QengineSyncUnavailable as exc:
        raise _unavailable(exc) from exc


@router.get("/health")
def health():
    """最近一次全量同步的健康摘要。"""
    try:
        return svc.fetch_health()
    except svc.QengineSyncUnavailable as exc:
        raise _unavailable(exc) from exc


@router.get("/pool-candidates")
def pool_candidates(
    asof: str | None = Query(default=None, pattern=r"^\d{8}$"),
    sid: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    """全量候选分页只读视图（后续候选/审计页使用）。"""
    try:
        return svc.fetch_pool_candidates(
            asof=asof, sid=sid, limit=limit, offset=offset
        )
    except svc.QengineSyncUnavailable as exc:
        raise _unavailable(exc) from exc
