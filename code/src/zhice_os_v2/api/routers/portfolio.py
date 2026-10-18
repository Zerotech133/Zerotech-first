"""组合 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from zhice_os_v2.api.dependencies import get_current_user, get_db
from zhice_os_v2.application.services.portfolio_service import PortfolioService
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

router = APIRouter(prefix="/portfolio", tags=["portfolio"], dependencies=[Depends(get_current_user)])


@router.get("/summary")
def summary(db: PersistencePort = Depends(get_db)):
    return PortfolioService(db).summary()
