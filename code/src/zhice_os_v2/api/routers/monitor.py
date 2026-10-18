"""监控 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from zhice_os_v2.api.dependencies import get_current_user, get_db
from zhice_os_v2.application.services.monitor_service import MonitorService
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

router = APIRouter(prefix="/monitor", tags=["monitor"], dependencies=[Depends(get_current_user)])


@router.get("/summary")
def summary(db: PersistencePort = Depends(get_db)):
    return MonitorService(db).summary()
