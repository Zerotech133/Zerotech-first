"""内存研究 Provider：用于 V2 开发/演示。

后续可以替换为外部研究引擎适配器，但对上层保持无感。
"""
from __future__ import annotations

from datetime import UTC, datetime

from zhice_os_v2.application.services.research_gateway import ResearchReport
from zhice_os_v2.domain.strategy.spec import FactorSpec, StrategySpec


class InMemoryResearchProvider:
    def __init__(self) -> None:
        self._reports: dict[str, ResearchReport] = {}

    def create_research(self, market: str, topic: str) -> ResearchReport:
        report_id = f"research-{len(self._reports) + 1}"
        spec = StrategySpec(
            spec_version=1,
            market=market,
            universe={"codes": ["600519.SH", "000001.SZ", "AAPL", "00700.HK", "BTC/USDT"][:3]},
            factors=[FactorSpec(name="momentum", params={"window": 5})],
            rules=[],
            research_ref=report_id,
        )
        report = ResearchReport(
            id=report_id,
            title=topic,
            market=market,
            status="COMPLETED",
            created_at=datetime.now(UTC),
            summary="memory research provider",
            strategy_spec=spec,
            source="external_research",
        )
        self._reports[report_id] = report
        return report

    def get_report(self, report_id: str) -> ResearchReport:
        try:
            return self._reports[report_id]
        except KeyError:
            raise KeyError(report_id) from None
