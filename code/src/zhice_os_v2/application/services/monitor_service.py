"""监控总览服务。"""
from __future__ import annotations

from zhice_os_v2.application.services.alert_service import AlertService
from zhice_os_v2.application.services.decay_monitor import DecayMonitor
from zhice_os_v2.application.services.portfolio_service import PortfolioService
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort


class MonitorService:
    def __init__(self, db: PersistencePort) -> None:
        self._db = db

    def summary(self) -> dict:
        return {
            "portfolio": PortfolioService(self._db).summary(),
            "decay": DecayMonitor(self._db).monitor(),
            "alerts": [
                {
                    "alert_id": a.alert_id,
                    "level": a.level,
                    "title": a.title,
                    "status": a.status,
                }
                for a in AlertService(self._db).list_alerts()
            ],
        }
