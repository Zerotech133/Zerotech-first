"""交易授权 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from zhice_os_v2.api.dependencies import (
    get_approval_center,
    get_approval_execution_service,
    get_current_user,
    get_db,
)
from zhice_os_v2.application.services.approval_center import ApprovalCenter
from zhice_os_v2.application.services.approval_execution_service import ApprovalExecutionService
from zhice_os_v2.application.services.execution_orchestrator import (
    LiveExecutionNotConfigured,
)
from zhice_os_v2.application.services.opening_submission_guard import (
    OpeningSubmissionDenied,
)
from zhice_os_v2.domain.market.context import MarketContext
from zhice_os_v2.domain.market.registry import get_profile
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

router = APIRouter(prefix="/approvals", tags=["approvals"], dependencies=[Depends(get_current_user)])


class CreateApprovalBody(BaseModel):
    market: str
    strategy_version_id: int
    order_intent_id: str
    ts_code: str
    direction: str
    qty: float
    price: float | None = None
    reason: str = ""


@router.post("")
def create_approval(
    body: CreateApprovalBody,
    center: ApprovalCenter = Depends(get_approval_center),
    db: PersistencePort = Depends(get_db),
):
    req = center.create_approval(
        market=body.market,
        strategy_version_id=str(body.strategy_version_id),
        order_intent_id=body.order_intent_id,
        ts_code=body.ts_code,
        direction=body.direction,
        qty=body.qty,
        price=body.price,
        reason=body.reason,
    )
    db.save_approval(req)
    return {
        "request_id": req.request_id,
        "status": req.status,
        "channel": req.channel,
        "auto_follow": req.auto_follow,
    }


class DecideBody(BaseModel):
    actor: str = "user"
    reason: str | None = None


@router.post("/{request_id:path}/approve")
def approve_request(
    request_id: str,
    body: DecideBody,
    center: ApprovalCenter = Depends(get_approval_center),
    execution: ApprovalExecutionService = Depends(get_approval_execution_service),
    db: PersistencePort = Depends(get_db),
):
    req = db.get_approval(request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="approval not found")
    try:
        center.approve(req, actor=body.actor)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.save_approval(req)  # 落库：get_approval 返回游离副本，center.approve 仅改内存对象
    profile = get_profile(req.market)
    ctx = MarketContext(market=req.market, profile=profile, currency=profile.currency, tz=profile.tz)
    try:
        positions = execution.execute_approved(req, ctx)
    except OpeningSubmissionDenied as exc:
        # 开仓门禁拒绝（如撤销会员的自建私有实例）→ 402 付费墙语义；claim 已在
        # execute_approved 回滚为 APPROVED，approval 未固着 EXECUTING。
        raise HTTPException(status_code=402, detail={"code": exc.code}) from exc
    except LiveExecutionNotConfigured as exc:
        # 实盘未配可靠外部撤单出箱 → 409 冲突（当前状态不允许实盘提交），非 500。
        raise HTTPException(status_code=409, detail={"code": exc.code}) from exc
    return {
        "request_id": req.request_id,
        "status": req.status,
        "positions": [p.position_id for p in positions],
    }


@router.post("/{request_id:path}/reject")
def reject_request(
    request_id: str,
    body: DecideBody,
    center: ApprovalCenter = Depends(get_approval_center),
    db: PersistencePort = Depends(get_db),
):
    req = db.get_approval(request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="approval not found")
    try:
        center.reject(req, actor=body.actor, reason=body.reason or "rejected")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.save_approval(req)  # 落库：同 approve，get_approval 是游离副本
    return {"request_id": req.request_id, "status": req.status}


@router.get("")
def list_approvals(db: PersistencePort = Depends(get_db)):
    return [
        {
            "request_id": r.request_id,
            "market": r.market,
            "strategy_version_id": r.strategy_version_id,
            "ts_code": r.ts_code,
            "direction": r.direction,
            "qty": r.qty,
            "status": r.status,
            "channel": r.channel,
            "auto_follow": r.auto_follow,
        }
        for r in db.list_approvals()
    ]
