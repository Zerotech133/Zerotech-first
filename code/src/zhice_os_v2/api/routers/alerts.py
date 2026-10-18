"""告警 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from zhice_os_v2.api.dependencies import deny_viewer, get_current_user, get_db
from zhice_os_v2.application.services.alert_service import AlertService
from zhice_os_v2.domain.alert.alert import AlertLevel, AlertStatus

router = APIRouter(prefix="/alerts", tags=["alerts"], dependencies=[Depends(get_current_user)])


def get_alert_service() -> AlertService:
    # 经 get_db 构造，随 persistence_backend 切换（sql/memory）；get_db 为单例，
    # 测试 conftest 清缓存后即得全新内存库，保证用例隔离。
    return AlertService(get_db())


class CreateAlertBody(BaseModel):
    level: AlertLevel = AlertLevel.INFO
    title: str
    message: str = ""


@router.post("")
def create_alert(body: CreateAlertBody, service: AlertService = Depends(get_alert_service)):
    alert = service.create_alert(body.level, body.title, body.message)
    return {"alert_id": alert.alert_id, "level": alert.level, "title": alert.title, "status": alert.status}


@router.get("")
def list_alerts(
    status: AlertStatus | None = None,
    service: AlertService = Depends(get_alert_service),
    _guard=Depends(deny_viewer),
):
    return [
        {
            "alert_id": a.alert_id,
            "level": a.level,
            "title": a.title,
            "message": a.message,
            "status": a.status,
            "created_at": a.created_at.isoformat(),
        }
        for a in service.list_alerts(status=status)
    ]


@router.post("/{alert_id}/ack")
def ack_alert(alert_id: str, service: AlertService = Depends(get_alert_service)):
    try:
        alert = service.ack(alert_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="alert not found") from exc
    return {"alert_id": alert.alert_id, "status": alert.status}
