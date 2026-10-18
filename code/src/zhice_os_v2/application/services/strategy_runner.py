"""策略批量运行器。"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date

from zhice_os_v2.application.services.candidate_service import CandidateService
from zhice_os_v2.application.services.strategy_service import StrategyService
from zhice_os_v2.domain.strategy.strategy import StrategyStatus
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

BarProvider = Callable[[str, date, int], list[float] | None]


class StrategyRunner:
    def __init__(self, db: PersistencePort) -> None:
        self._db = db

    def run_all(
        self,
        trade_date: date,
        bar_provider: BarProvider,
        strategy_service: StrategyService,
        candidate_service: CandidateService,
    ) -> dict:
        results = []
        for version in self._db.list_strategy_versions():
            if version.status != StrategyStatus.ACTIVE:
                continue
            pool = candidate_service.generate(
                version.spec, trade_date, bar_provider,
                strategy_version_id=version.strategy_version_id,
            )
            results.append(
                {
                    "strategy_version_id": version.strategy_version_id,
                    "market": version.market,
                    "candidates": len(pool.candidates),
                }
            )
        return {"strategies": len(results), "results": results}
