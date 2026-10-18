"""自动化规则服务。"""
from __future__ import annotations

from zhice_os_v2.application.services.governance_service import GovernanceService
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

RULES = [
    {
        "name": "auto_demote_decayed",
        "description": "自动降级近期胜率明显下滑的策略",
        "enabled": False,
    },
]


class AutomationService:
    def __init__(self, db: PersistencePort) -> None:
        self._db = db

    def list_rules(self) -> list[dict]:
        return RULES

    def run_rules(self) -> dict:
        results = []
        for rule in RULES:
            if not rule["enabled"]:
                continue
            if rule["name"] == "auto_demote_decayed":
                suggestions = GovernanceService(self._db).auto_review()["suggestions"]
                demoted = []
                for s in suggestions:
                    try:
                        GovernanceService(self._db).demote(s["strategy_version_id"], s["reason"])
                        demoted.append(s["strategy_version_id"])
                    except Exception:
                        pass
                results.append({"rule": rule["name"], "demoted": demoted})
        return {"rules": results}
