"""交易执行全链路 Pipeline。

覆盖：候选生成 → 计划 → 风控 → 授权 → 执行 → 持仓 → 复盘 → 学习。
V2 第一版以内存仓储 + 模拟券商运行，后续可替换为持久化实现。
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal

from zhice_os_v2.application.services.approval_center import ApprovalCenter
from zhice_os_v2.application.services.candidate_service import CandidateService
from zhice_os_v2.application.services.execution_orchestrator import ExecutionOrchestrator
from zhice_os_v2.application.services.execution_service import ExecutionService
from zhice_os_v2.application.services.learning_service import LearningService
from zhice_os_v2.application.services.replay_service import ReplayService
from zhice_os_v2.application.services.risk_service import RiskService
from zhice_os_v2.application.services.strategy_service import StrategyService
from zhice_os_v2.application.services.trade_plan_service import TradePlanService
from zhice_os_v2.domain.execution.broker import OrderSide
from zhice_os_v2.domain.market.context import MarketContext
from zhice_os_v2.domain.portfolio.position import PositionStatus
from zhice_os_v2.domain.strategy.spec import StrategySpec
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

BarProvider = Callable[[str, date, int], list[float] | None]


class TradeExecutionPipeline:
    def __init__(
        self,
        db: PersistencePort,
        strategy_service: StrategyService,
        candidate_service: CandidateService,
        trade_plan_service: TradePlanService,
        risk_service: RiskService,
        approval_center: ApprovalCenter,
        execution_orchestrator: ExecutionOrchestrator,
        execution_service: ExecutionService,
        replay_service: ReplayService,
        learning_service: LearningService,
    ) -> None:
        self._db = db
        self._strategy_service = strategy_service
        self._candidate_service = candidate_service
        self._trade_plan_service = trade_plan_service
        self._risk_service = risk_service
        self._approval_center = approval_center
        self._execution_orchestrator = execution_orchestrator
        self._execution_service = execution_service
        self._replay_service = replay_service
        self._learning_service = learning_service

    def run(
        self,
        ctx: MarketContext,
        spec: StrategySpec,
        strategy_name: str,
        trade_date: date,
        bar_provider: BarProvider,
        ref_price: Decimal,
        qty: Decimal,
    ) -> dict:
        # 1. 注册策略
        version = self._strategy_service.register(strategy_name, ctx.market, spec)

        # 2. 生成候选（把 strategy_version_id 贯穿到候选池与候选，确保落库可归属）
        pool = self._candidate_service.generate(
            spec, trade_date, bar_provider,
            strategy_version_id=version.strategy_version_id,
        )
        if not pool.candidates:
            return {"status": "NO_CANDIDATE", "strategy_version_id": version.strategy_version_id}

        # 3. 取第一个候选生成计划
        candidate = pool.candidates[0]
        candidate.strategy_version_id = version.strategy_version_id
        plan = self._trade_plan_service.create_from_candidate(candidate, spec.risk, ref_price, qty)

        # 4. 风控
        risk = self._risk_service.check_plan(plan)
        if risk.result.value == "BLOCK":
            return {
                "status": "RISK_BLOCKED",
                "strategy_version_id": version.strategy_version_id,
                "reasons": risk.reasons,
            }

        # 5. 授权
        approval = self._approval_center.create_approval(
            market=ctx.market,
            strategy_version_id=version.strategy_version_id,
            order_intent_id=plan.plan_id,
            ts_code=candidate.ts_code,
            direction="BUY",
            qty=float(qty),
            price=float(ref_price),
            reason=candidate.reason,
        )
        self._db.save_approval(approval)

        # 6. 执行（模拟/实盘由开关决定）
        results = self._execution_orchestrator.execute(
            ctx,
            strategy_version_id=version.strategy_version_id,
            order_intent_id=approval.request_id,
            ts_code=candidate.ts_code,
            side=OrderSide.BUY,
            qty=qty,
            price=ref_price,
        )

        # 7. 持仓
        positions = []
        for result in results:
            position = self._execution_service.apply_fill(
                result,
                strategy_version_id=version.strategy_version_id,
                market=ctx.market,
                ts_code=candidate.ts_code,
                trade_plan_id=plan.plan_id,
            )
            if position:
                positions.append(position)

        # 8. 模拟平仓并生成复盘/学习（用于演示完整闭环）
        replay_ids = []
        learning_ids = []
        for position in positions:
            position.status = PositionStatus.CLOSED
            position.close_qty = position.open_qty
            position.close_price = ref_price * Decimal("1.02")
            replay = self._replay_service.build_replay_case(position)
            if replay:
                replay_ids.append(replay.replay_id)
                learning = self._learning_service.record_from_replay(replay)
                learning_ids.append(learning.learning_id)

        return {
            "status": "OK",
            "strategy_version_id": version.strategy_version_id,
            "candidate_id": candidate.candidate_id,
            "plan_id": plan.plan_id,
            "approval_id": approval.request_id,
            "approval_status": approval.status,
            "position_ids": [p.position_id for p in positions],
            "replay_ids": replay_ids,
            "learning_ids": learning_ids,
        }
