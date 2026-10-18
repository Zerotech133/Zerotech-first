"""参数优化服务（规则化学习的第一阶段）。

V2 早期使用简单网格搜索 + 候选覆盖率/平均得分作为启发式指标。
后续可替换为更严格的 walk-forward 回测。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import cast

from zhice_os_v2.application.services.candidate_service import BarProvider, CandidateService
from zhice_os_v2.domain.strategy.spec import StrategySpec
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort


class ParameterOptimizer:
    def __init__(self, db: PersistencePort) -> None:
        self._db = db

    def grid_search(
        self,
        spec: StrategySpec,
        trade_date: date,
        bar_provider: BarProvider,
        param_grids: list[dict],
    ) -> list[dict]:
        results = []
        service = CandidateService(self._db)
        for params in param_grids:
            trial = deepcopy(spec)
            if trial.factors:
                merged = {**trial.factors[0].params, **params}
                trial.factors[0].params = merged
            # 网格搜索是临时试参，只取分数，不落库（避免污染候选池表）
            pool = service.generate(trial, trade_date, bar_provider, persist=False)
            scores = [c.score for c in pool.candidates]
            results.append(
                {
                    "params": params,
                    "candidate_count": len(scores),
                    "avg_score": sum(scores) / len(scores) if scores else 0.0,
                    "max_score": max(scores) if scores else 0.0,
                }
            )
        results.sort(key=lambda r: cast(float, r["avg_score"]), reverse=True)
        return results
