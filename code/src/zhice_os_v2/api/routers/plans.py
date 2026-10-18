"""交易计划 API（V1 plans 对齐）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import func

from zhice_os_v2.api.dependencies import get_approval_center, get_current_user, get_db
from zhice_os_v2.application.services.account_stats_service import AccountStatsService
from zhice_os_v2.application.services.approval_center import ApprovalCenter
from zhice_os_v2.domain.security.user import User
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

router = APIRouter(prefix="/plans", tags=["plans"], dependencies=[Depends(get_current_user)])


def _plan_rows(market: str, strategy: str | None, status: str | None, limit: int):
    """查询计划 + 最新风控结论（LEFT JOIN risk_check/risk_decision，多轮取最新）。"""
    session = get_session()
    try:
        # 每 plan 最新一轮检查（id 最大）→ 对应 check_id
        max_ids = (
            session.query(
                orm.RiskCheckModel.plan_id.label("plan_id"),
                func.max(orm.RiskCheckModel.id).label("max_id"),
            )
            .group_by(orm.RiskCheckModel.plan_id)
            .subquery()
        )
        latest_check = (
            session.query(
                orm.RiskCheckModel.plan_id.label("plan_id"),
                orm.RiskCheckModel.check_id.label("check_id"),
            )
            .join(
                max_ids,
                (max_ids.c.plan_id == orm.RiskCheckModel.plan_id)
                & (max_ids.c.max_id == orm.RiskCheckModel.id),
            )
            .subquery()
        )
        q = (
            session.query(
                orm.TradePlanModel,
                orm.RiskDecisionModel.decision_result,
                orm.RiskDecisionModel.rule_results,
            )
            .outerjoin(latest_check, latest_check.c.plan_id == orm.TradePlanModel.plan_id)
            .outerjoin(
                orm.RiskDecisionModel,
                orm.RiskDecisionModel.check_id == latest_check.c.check_id,
            )
            .filter(orm.TradePlanModel.market == market)
            .order_by(orm.TradePlanModel.created_at.desc())
        )
        if strategy:
            q = q.filter(orm.TradePlanModel.strategy_version_id == strategy)
        if status:
            q = q.filter(orm.TradePlanModel.plan_status == status)
        return q.limit(limit).all()
    finally:
        session.close()


def _num(v) -> float | None:
    return float(v) if v is not None else None


@router.get("")
def list_plans(
    market: str = "CN",
    strategy: str | None = None,
    status: str | None = None,
    limit: int = 200,
):
    """计划列表：join 风控结论 + 策略名。按 created_at 倒序。"""
    rows = _plan_rows(market, strategy, status, limit)
    sv_names = AccountStatsService._strategy_names()
    return [
        {
            "plan_id": plan.plan_id,
            "candidate_id": plan.candidate_id,
            "ts_code": plan.ts_code,
            "strategy_version_id": plan.strategy_version_id,
            "strategy_name": sv_names.get(plan.strategy_version_id, plan.strategy_version_id),
            "plan_status": plan.plan_status,
            "ref_price": _num(plan.ref_price),
            "stop_loss_price": _num(plan.stop_loss_price),
            "take_profit_price": _num(plan.take_profit_price),
            "confidence_level": _num(plan.confidence_level),
            "risk_result": risk_result,
            "risk_rules": risk_rules,
            "created_at": plan.created_at.isoformat() if plan.created_at else None,
        }
        for plan, risk_result, risk_rules in rows
    ]


class ApproveBody(BaseModel):
    approver: str = "user"


class RejectBody(BaseModel):
    approver: str = "user"
    reason: str

    @field_validator("reason")
    @classmethod
    def _reason_required(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("reason is required")
        return v.strip()


def _update_plan_status(plan_id: str, new_status: str) -> dict:
    session = get_session()
    try:
        plan = (
            session.query(orm.TradePlanModel)
            .filter(orm.TradePlanModel.plan_id == plan_id)
            .one_or_none()
        )
        if plan is None:
            raise HTTPException(status_code=404, detail="plan not found")
        plan.plan_status = new_status
        session.commit()
        return {
            "plan_id": plan.plan_id,
            "ts_code": plan.ts_code,
            "plan_status": plan.plan_status,
        }
    finally:
        session.close()


def _create_approval_for_plan(
    plan_id: str,
    db: PersistencePort,
    center: ApprovalCenter,
    actor: str,
) -> dict:
    """为已批准的计划自动创建 ApprovalRequest（幂等）。

    使用 plan_id 作为 order_intent_id，因此 request_id = f"apr-{plan_id}" 确定性唯一。
    重复调用时：若同 request_id 已存在则直接返回 auto_created=False，不重复落库。
    计划不存在时返回空 dict（已被 _update_plan_status 的 404 兜底）。
    """
    plan = db.get_trade_plan(plan_id)
    if plan is None:
        return {}

    request_id = f"apr-{plan_id}"
    existing = db.get_approval(request_id)
    if existing is not None:
        return {"approval_request_id": existing.request_id, "auto_created": False}

    req = center.create_approval(
        market=plan.market,
        strategy_version_id=plan.strategy_version_id,
        order_intent_id=plan_id,
        ts_code=plan.ts_code,
        direction=plan.direction,
        qty=float(plan.qty),
        price=float(plan.ref_price) if plan.ref_price is not None else None,
        reason=f"auto-created on plan approval by {actor}",
    )
    db.save_approval(req)
    return {"approval_request_id": req.request_id, "auto_created": True}


@router.post("/{plan_id}/approve")
def approve_plan(
    plan_id: str,
    body: ApproveBody,
    current_user: User = Depends(get_current_user),
    db: PersistencePort = Depends(get_db),
    center: ApprovalCenter = Depends(get_approval_center),
):
    """计划批准：plan_status='APPROVED'，并自动创建 PENDING ApprovalRequest（幂等）。

    一次批准即完成双轨：plan_status 变更 + ApprovalRequest 落库。
    重复调用时 ApprovalRequest 不会重复创建（基于 request_id=apr-{plan_id} 幂等）。
    """
    result = _update_plan_status(plan_id, "APPROVED")
    actor = current_user.username if current_user else body.approver
    extra = _create_approval_for_plan(plan_id, db, center, actor)
    return {**result, **extra}


@router.post("/{plan_id}/reject")
def reject_plan(plan_id: str, body: RejectBody):
    """计划拒绝：plan_status='REJECTED'（reason 必填）。"""
    return _update_plan_status(plan_id, "REJECTED")
