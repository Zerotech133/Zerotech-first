"""授权通过后执行服务。"""
from __future__ import annotations

import logging
from decimal import Decimal

from zhice_os_v2.application.services.execution_orchestrator import (
    ExecutionOrchestrator,
    LiveExecutionNotConfigured,
)
from zhice_os_v2.application.services.execution_service import ExecutionService
from zhice_os_v2.application.services.opening_submission_guard import (
    OpeningSubmissionDenied,
)
from zhice_os_v2.domain.approval.approval import ApprovalRequest, ApprovalStatus
from zhice_os_v2.domain.execution.broker import OrderSide, OrderStatus
from zhice_os_v2.domain.market.context import MarketContext
from zhice_os_v2.domain.portfolio.position import PositionLot
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

logger = logging.getLogger(__name__)


class ApprovalExecutionService:
    def __init__(
        self,
        db: PersistencePort,
        orchestrator: ExecutionOrchestrator,
        execution_service: ExecutionService,
    ) -> None:
        self._db = db
        self._orchestrator = orchestrator
        self._execution_service = execution_service

    def execute_approved(self, approval: ApprovalRequest, ctx: MarketContext) -> list[PositionLot]:
        if approval.status != ApprovalStatus.APPROVED:
            raise ValueError("approval is not approved")
        # 原子性占位：APPROVED → EXECUTING，防止并发/重复执行（路由 + 盘中调度器双路）。
        # claim 返回 False 意味着另一路径已领走执行权（EXECUTING/EXECUTED），直接跳过。
        if not self._db.claim_approval_for_execution(approval.request_id):
            raise ValueError(
                f"approval {approval.request_id} is already EXECUTING or EXECUTED"
            )
        try:
            results = self._orchestrator.execute(
                ctx,
                strategy_version_id=approval.strategy_version_id,
                order_intent_id=approval.request_id,
                ts_code=approval.ts_code,
                side=OrderSide(approval.direction),
                qty=Decimal(str(approval.qty)),
                price=Decimal(str(approval.price or 0)),
                # B2-3：带归属时经开仓门禁复核会员（BUY 才判定，SELL 天然不过门禁）。
                user_strategy_id=approval.user_strategy_id,
            )
        except (OpeningSubmissionDenied, LiveExecutionNotConfigured):
            # B2-3 fail-closed：orchestrator 在提交边界拒绝（撤会员开仓 / 无实盘出箱）。
            # 已 claim(APPROVED→EXECUTING) 但未成交 → 回滚 claim，杜绝 approval 永卡
            # EXECUTING 占 sim_max_positions 配额；再 re-raise 让上游按 code 映射响应。
            self._db.release_approval_claim(approval.request_id)
            raise
        positions = []
        over_sell_detected = False
        _sell_side = str(approval.direction).upper() == "SELL"
        for result in results:
            position = self._execution_service.apply_fill(
                result,
                strategy_version_id=str(approval.strategy_version_id),
                market=approval.market,
                ts_code=approval.ts_code,
                side=approval.direction,
                # 归属链：approval.order_intent_id == plan.plan_id，写入 v2_position.trade_plan_id
                # 确保人工审批/API 执行路径与 _auto_execute 自动执行路径口径一致，不丢 plan 关联。
                trade_plan_id=approval.order_intent_id,
            )
            if position:
                positions.append(position)
            elif (
                _sell_side
                and result.status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED)
                and result.filled_qty > 0
            ):
                # apply_fill 对 SELL 返回 None → 超持仓（已平部分已落库，但有余量无法平仓）
                over_sell_detected = True
                logger.warning(
                    "超持仓卖出检测：approval=%s ts_code=%s filled_qty=%s 持仓不足，部分已平仓入账",
                    approval.request_id, approval.ts_code, result.filled_qty,
                )

        if over_sell_detected:
            # 超持仓：已平部分不回滚，但标记为 PARTIALLY_EXECUTED 而非 EXECUTED，供人工核查
            logger.warning(
                "approval %s 超持仓卖出，标记 PARTIALLY_EXECUTED，请核查剩余持仓",
                approval.request_id,
            )
            approval.status = ApprovalStatus.PARTIALLY_EXECUTED
        else:
            # 执行完成：标记终态 EXECUTED，阻止任何后续重复执行尝试。
            approval.status = ApprovalStatus.EXECUTED
        # 直改状态（非 save_approval）：save_approval 把 EXECUTING 当不可覆写终态会拦下
        # EXECUTING→终态，致已成交承认永卡 EXECUTING 占 sim_max_positions（与 _auto_execute 同修）。
        if not self._db.mark_approval_executed(approval.request_id, approval.status.value):
            # 兜底：claim 态异常时仍尝试 save（保持旧行为不静默丢状态）
            self._db.save_approval(approval)
        return positions
