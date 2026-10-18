"""风控 API（V1 risk 对齐）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from zhice_os_v2.api.dependencies import get_current_user
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session

router = APIRouter(prefix="/risk", tags=["risk"], dependencies=[Depends(get_current_user)])


@router.get("/checks")
def risk_checks(market: str = "CN", result: str | None = None, limit: int = 100):
    """风控审查列表：risk_check JOIN risk_decision JOIN trade_plan（取 ts_code）。

    result 参数过滤（兼容 V1 语义：WARNING → PASS_WITH_WARNING，BLOCK → REJECT），
    按 created_at 倒序。
    """
    # V1 语义别名 → V2 实际枚举
    result_map = {"WARNING": "PASS_WITH_WARNING", "BLOCK": "REJECT"}
    normalized = result_map.get(result, result) if result else None

    session = get_session()
    try:
        stmt = (
            session.query(
                orm.RiskCheckModel.check_id,
                orm.RiskCheckModel.plan_id,
                orm.RiskCheckModel.check_stage,
                orm.RiskCheckModel.check_round,
                orm.TradePlanModel.ts_code,
                orm.TradePlanModel.strategy_version_id,
                orm.RiskDecisionModel.decision_id,
                orm.RiskDecisionModel.decision_result,
                orm.RiskDecisionModel.warning_flag,
                orm.RiskDecisionModel.rule_results,
                orm.RiskDecisionModel.approver_type,
                orm.RiskCheckModel.created_at,
            )
            .join(orm.RiskDecisionModel, orm.RiskDecisionModel.check_id == orm.RiskCheckModel.check_id)
            .join(orm.TradePlanModel, orm.TradePlanModel.plan_id == orm.RiskCheckModel.plan_id)
            .filter(orm.RiskCheckModel.market == market)
        )
        if normalized:
            stmt = stmt.filter(orm.RiskDecisionModel.decision_result == normalized)
        rows = (
            stmt.order_by(orm.RiskCheckModel.created_at.desc())
            .limit(max(1, min(limit, 1000)))
            .all()
        )
    finally:
        session.close()

    return [
        {
            "check_id": r.check_id,
            "plan_id": r.plan_id,
            "ts_code": r.ts_code,
            "strategy_version_id": r.strategy_version_id,
            "check_stage": r.check_stage,
            "check_round": r.check_round,
            "decision_id": r.decision_id,
            "decision_result": r.decision_result,
            "warning_flag": bool(r.warning_flag),
            "rule_results": r.rule_results,
            "approver_type": r.approver_type,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.get("/deviation")
def execution_deviation(market: str = "CN", limit: int = 100):
    """执行偏差分析：fill vs intent（沿 execution→request→intent 链取意向价）。

    deviation_pct = (fill - intent) / intent * 100，正 = 买贵/卖亏，按 fill_time 倒序。
    """
    session = get_session()
    try:
        rows = (
            session.query(
                orm.FillRecordModel.ts_code,
                orm.FillRecordModel.fill_price,
                orm.FillRecordModel.fill_qty,
                orm.FillRecordModel.fill_time,
                orm.OrderIntentModel.intent_price,
            )
            .join(
                orm.ExecutionRecordModel,
                orm.ExecutionRecordModel.execution_id == orm.FillRecordModel.execution_id,
            )
            .join(
                orm.OrderRequestModel,
                orm.OrderRequestModel.request_id == orm.ExecutionRecordModel.request_id,
            )
            .join(
                orm.OrderIntentModel,
                orm.OrderIntentModel.intent_id == orm.OrderRequestModel.intent_id,
            )
            .filter(
                orm.FillRecordModel.market == market,
                orm.FillRecordModel.fill_price.isnot(None),
                orm.OrderIntentModel.intent_price.isnot(None),
                orm.OrderIntentModel.intent_price != 0,
            )
            .order_by(orm.FillRecordModel.fill_time.desc().nullslast())
            .limit(max(1, min(limit, 1000)))
            .all()
        )
    finally:
        session.close()

    records = []
    deviations: list[float] = []
    for r in rows:
        fill_price = float(r.fill_price)
        intent_price = float(r.intent_price)
        deviation_pct = (fill_price - intent_price) / intent_price * 100
        deviations.append(deviation_pct)
        records.append(
            {
                "ts_code": r.ts_code,
                "fill_price": fill_price,
                "intent_price": intent_price,
                "deviation_pct": round(deviation_pct, 4),
                "fill_qty": float(r.fill_qty) if r.fill_qty is not None else None,
                "fill_time": r.fill_time.isoformat() if r.fill_time else None,
            }
        )

    avg_deviation_pct = round(sum(deviations) / len(deviations), 4) if deviations else None
    return {"avg_deviation_pct": avg_deviation_pct, "records": records}
