"""策略执行器：统一策略到候选信号的接口。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from zhice_os_v2.domain.market.context import MarketContext
from zhice_os_v2.domain.strategy.spec import StrategySpec


@dataclass(frozen=True)
class CandidateSignal:
    market: str
    ts_code: str
    score: float
    reason: str
    factor_values: dict[str, float]


class StrategyExecutor(Protocol):
    def generate_candidates(
        self,
        ctx: MarketContext,
        spec: StrategySpec,
        trade_date: date,
    ) -> list[CandidateSignal]: ...
