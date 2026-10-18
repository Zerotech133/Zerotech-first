"""自动化 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from zhice_os_v2.api.dependencies import get_current_user, get_db
from zhice_os_v2.application.services.automation_service import AutomationService
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

router = APIRouter(prefix="/automation", tags=["automation"], dependencies=[Depends(get_current_user)])


@router.get("/rules")
def list_rules(db: PersistencePort = Depends(get_db)):
    return AutomationService(db).list_rules()


@router.post("/run")
def run_rules(db: PersistencePort = Depends(get_db)):
    return AutomationService(db).run_rules()
