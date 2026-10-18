"""交易 API：提供模拟全链路执行入口。"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from zhice_os_v2.api.dependencies import (
    get_approval_center,
    get_candidate_service,
    get_current_user,
    get_db,
    get_execution_orchestrator,
    get_execution_service,
    get_learning_service,
    get_replay_service,
    get_risk_service,
    get_strategy_service,
    get_trade_plan_service,
)
from zhice_os_v2.application.services.trade_execution_pipeline import TradeExecutionPipeline
from zhice_os_v2.domain.market.context import MarketContext
from zhice_os_v2.domain.market.registry import get_profile
from zhice_os_v2.domain.strategy.spec import StrategySpec
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

router = APIRouter(prefix="/trade", tags=["trade"], dependencies=[Depends(get_current_user)])


class SimulateBody(BaseModel):
    market: str
    strategy_name: str
    spec: dict
    trade_date: str | None = None
    ref_price: float = 100.0
    qty: float = 100.0


@router.post("/simulate")
def simulate(body: SimulateBody, db: PersistencePort = Depends(get_db)):
    spec = StrategySpec.model_validate(body.spec)
    profile = get_profile(body.market)
    ctx = MarketContext(market=body.market, profile=profile, currency=profile.currency, tz=profile.tz)
    trade_date = date.fromisoformat(body.trade_date) if body.trade_date else date.today()

    def bars(ts_code: str, td: date, window: int):
        return [100 + i for i in range(window)]

    pipeline = TradeExecutionPipeline(
        db=db,
        strategy_service=get_strategy_service(),
        candidate_service=get_candidate_service(),
        trade_plan_service=get_trade_plan_service(),
        risk_service=get_risk_service(),
        approval_center=get_approval_center(),
        execution_orchestrator=get_execution_orchestrator(),
        execution_service=get_execution_service(),
        replay_service=get_replay_service(),
        learning_service=get_learning_service(),
    )
    try:
        result = pipeline.run(
            ctx=ctx,
            spec=spec,
            strategy_name=body.strategy_name,
            trade_date=trade_date,
            bar_provider=bars,
            ref_price=Decimal(str(body.ref_price)),
            qty=Decimal(str(body.qty)),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result
