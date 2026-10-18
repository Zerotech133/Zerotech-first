"""工作流 API。"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends

from zhice_os_v2.api.dependencies import get_current_user, get_db
from zhice_os_v2.application.services.workflow_service import WorkflowService
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

router = APIRouter(prefix="/workflow", tags=["workflow"], dependencies=[Depends(get_current_user)])


@router.post("/pre-market")
def pre_market(
    market: str = "CN",
    trade_date: str | None = None,
    strategy_type: str = "TREND",
    db: PersistencePort = Depends(get_db),
):
    td = date.fromisoformat(trade_date) if trade_date else date.today()
    return WorkflowService(db).run_pre_market(market, td, strategy_type=strategy_type)


@router.post("/intraday")
def intraday(market: str = "CN", db: PersistencePort = Depends(get_db)):
    return WorkflowService(db).run_intraday(market)


@router.post("/post-market")
def post_market(market: str = "CN", db: PersistencePort = Depends(get_db)):
    return WorkflowService(db).run_post_market(market)
