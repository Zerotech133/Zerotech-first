"""研究网关：对外统一“智策研究引擎”，内部可对接外部研究 Provider。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from zhice_os_v2.domain.strategy.spec import StrategySpec


@dataclass(frozen=True)
class ResearchReport:
    id: str
    title: str
    market: str
    status: str
    created_at: datetime
    summary: str = ""
    backtest_perf: dict | None = None
    strategy_spec: StrategySpec | None = None
    source: str = "external_research"  # 用户不可见


class ResearchProvider(Protocol):
    def create_research(self, market: str, topic: str) -> ResearchReport: ...
    def get_report(self, report_id: str) -> ResearchReport: ...


class ResearchGateway:
    """对上层隐藏 Provider 细节，用户只感知智策研究引擎。"""

    def __init__(self, provider: ResearchProvider) -> None:
        self._provider = provider

    def start_research(self, market: str, topic: str) -> ResearchReport:
        return self._provider.create_research(market, topic)

    def get_research(self, report_id: str) -> ResearchReport:
        return self._provider.get_report(report_id)
