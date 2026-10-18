"""演示：跑通 V2 全链路交易 Pipeline。"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from zhice_os_v2.application.services.approval_center import ApprovalCenter, StrategySwitch
from zhice_os_v2.application.services.candidate_service import CandidateService
from zhice_os_v2.application.services.execution_orchestrator import ExecutionOrchestrator
from zhice_os_v2.application.services.execution_service import ExecutionService
from zhice_os_v2.application.services.learning_service import LearningService
from zhice_os_v2.application.services.replay_service import ReplayService
from zhice_os_v2.application.services.risk_service import RiskService
from zhice_os_v2.application.services.strategy_service import StrategyService
from zhice_os_v2.application.services.trade_execution_pipeline import TradeExecutionPipeline
from zhice_os_v2.application.services.trade_plan_service import TradePlanService
from zhice_os_v2.domain.execution.broker import ExecutionMode
from zhice_os_v2.domain.market.context import MarketContext
from zhice_os_v2.domain.market.registry import get_profile
from zhice_os_v2.domain.strategy.spec import StrategySpec
from zhice_os_v2.infrastructure.brokers.simulated import SimulatedBroker
from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase


def _bars(ts_code: str, td: date, window: int):
    return [100 + i for i in range(window)]


def main() -> None:
    db = InMemoryDatabase()
    switches = {("CN", 1): StrategySwitch(market="CN", strategy_version_id=1, simulated_enabled=True)}
    pipeline = TradeExecutionPipeline(
        db=db,
        strategy_service=StrategyService(db),
        candidate_service=CandidateService(db),
        trade_plan_service=TradePlanService(db),
        risk_service=RiskService(),
        approval_center=ApprovalCenter(switches),
        execution_orchestrator=ExecutionOrchestrator(
            brokers={ExecutionMode.SIMULATED: SimulatedBroker()}, switches=switches
        ),
        execution_service=ExecutionService(db),
        replay_service=ReplayService(db),
        learning_service=LearningService(db),
    )
    spec = StrategySpec(
        spec_version=1,
        market="CN",
        universe={"codes": ["600519.SH"]},
        factors=[{"name": "momentum", "params": {"window": 5}}],
        rules=[],
    )
    profile = get_profile("CN")
    ctx = MarketContext(market="CN", profile=profile, currency=profile.currency, tz=profile.tz)
    result = pipeline.run(
        ctx=ctx,
        spec=spec,
        strategy_name="Demo",
        trade_date=date(2026, 8, 18),
        bar_provider=_bars,
        ref_price=Decimal("100"),
        qty=Decimal("100"),
    )
    print(result)


if __name__ == "__main__":
    main()
