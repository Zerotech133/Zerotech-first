"""学习报告：汇总复盘、衰减、治理建议。"""
from __future__ import annotations

from zhice_os_v2.application.services.decay_monitor import DecayMonitor
from zhice_os_v2.application.services.governance_service import GovernanceService
from zhice_os_v2.application.services.report_service import ReportService
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort


class LearningReportService:
    def __init__(self, db: PersistencePort) -> None:
        self._db = db

    def generate(self, market: str | None = None) -> dict:
        return {
            "replay": ReportService(self._db).replay_report(market=market),
            "decay": DecayMonitor(self._db).monitor(),
            "governance": GovernanceService(self._db).auto_review(),
        }
