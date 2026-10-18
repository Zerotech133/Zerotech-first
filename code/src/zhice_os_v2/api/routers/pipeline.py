"""交易闭环 Pipeline API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from zhice_os_v2.api.dependencies import get_current_user, get_db
from zhice_os_v2.application.services.pipeline_service import PipelineService
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

router = APIRouter(prefix="/pipeline", tags=["pipeline"], dependencies=[Depends(get_current_user)])


@router.get("")
def pipeline(
    market: str = "CN",
    strategy: str | None = None,
    db: PersistencePort = Depends(get_db),
):
    svc = PipelineService(db)
    return {
        "funnel": svc.funnel(market, strategy=strategy),
        "decisions": svc.decisions(market, strategy=strategy),
    }
