"""授权通过后执行服务。

P0-2 SELL 守卫（T+1 / 可卖量 / 停牌 / 归属）：
``ApprovalExecutionService.execute_approved`` 在 claim（APPROVED→EXECUTING）与 broker 提交
**之前**先调用 ``ExecutionService.sell_guard``：
- 拒绝时抛 ``SellGuardDenied``（``LiveExecutionNotConfigured`` 子类，路由按 409 返回稳定
  机读 code=``sell.*``），approval 保持 APPROVED，不占用 EXECUTING/配额；
- 通过时把有效卖出量（普通超卖会 clamp 到可卖量）传给 orchestrator，并把归属透传到
  ``ExecutionService.apply_fill(enforce_sell_guard=True)``——即使 broker 成交超过可卖量，
  FIFO 也绝不消耗当日 lot/他人账户 lot，剩余按 PARTIALLY_EXECUTED 处理。
- 停牌/跌停：复用 ``market_rules.can_sell``（审批价≤0 → 停牌/无行情拒绝；快照陈旧/跌停
  封死需调用方携带 snapshot/pre_close，当前审批链只有 price 时先拦价≤0）。
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

from zhice_os_v2.application.services.execution_orchestrator import (
    ExecutionOrchestrator,
    LiveExecutionNotConfigured,
)
from zhice_os_v2.application.services.execution_service import (
    ExecutionService,
    SELL_DENY_INVALID_QTY,
    SELL_DENY_NO_POSITION,
    SELL_DENY_OPENED_AT_MISSING,
    SELL_DENY_OWNER_MISMATCH,
    SELL_DENY_SUSPENDED,
    SELL_DENY_T1_LOCKED,
)
from zhice_os_v2.application.services.opening_submission_guard import (
    OpeningSubmissionDenied,
)
from zhice_os_v2.domain.approval.approval import ApprovalRequest, ApprovalStatus
from zhice_os_v2.domain.execution.broker import OrderSide, OrderStatus
from zhice_os_v2.domain.market.context import MarketContext
from zhice_os_v2.domain.portfolio.position import PositionLot
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

logger = logging.getLogger(__name__)


class SellGuardDenied(LiveExecutionNotConfigured):
    """SELL 守卫拒绝（T+1 / 可卖量 / 停牌 / 归属）。

    继承 ``LiveExecutionNotConfigured`` 以复用审批路由既有的「前置条件不满足 → 409 +
    稳定机读 code」映射，不新增路由改动；``code`` 取 ``sell.*`` 之一，``details`` 携带
    requested/sellable/t1_locked 等证据供前端/告警展示。
    """

    def __init__(self, code: str, reason: str = "", details: dict | None = None) -> None:
        super().__init__(code)
        self.reason = reason
        self.details = details or {}


SELL_GUARD_CODES = {
    SELL_DENY_NO_POSITION,
    SELL_DENY_T1_LOCKED,
    SELL_DENY_OPENED_AT_MISSING,
    SELL_DENY_OWNER_MISMATCH,
    SELL_DENY_SUSPENDED,
    SELL_DENY_INVALID_QTY,
}


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

    def execute_approved(
        self,
        approval: ApprovalRequest,
        ctx: MarketContext,
        now: datetime | None = None,
    ) -> list[PositionLot]:
        if approval.status != ApprovalStatus.APPROVED:
            raise ValueError("approval is not approved")

        _sell_side = str(approval.direction).upper() == "SELL"
        requested_qty = Decimal(str(approval.qty))
        effective_qty = requested_qty
        guard_info: dict | None = None

        # P0-2：SELL 在 claim 与 broker 提交前先过统一守卫（T+1/可卖量/停牌/归属）。
        # 拒绝时不改 approval 状态（保持 APPROVED，供次日/人工处理后重试），不占 EXECUTING。
        if _sell_side:
            guard_info = self._execution_service.sell_guard(
                market=approval.market,
                strategy_version_id=str(approval.strategy_version_id),
                ts_code=approval.ts_code,
                qty=requested_qty,
                user_strategy_id=approval.user_strategy_id,
                account_id=approval.account_id,
                price=approval.price,
                now=now,
            )
            if guard_info.get("denied"):
                code = str(guard_info.get("deny_code") or "sell.denied")
                reason = str(guard_info.get("deny_reason") or "SELL 守卫拒绝")
                logger.warning(
                    "SELL 守卫拒绝 approval=%s market=%s ts_code=%s code=%s reason=%s details=%s",
                    approval.request_id, approval.market, approval.ts_code, code, reason,
                    {k: guard_info.get(k) for k in (
                        "requested_qty", "sellable_qty", "t1_locked_qty",
                        "ownership_excluded_qty", "opened_at_missing_qty")},
                )
                raise SellGuardDenied(code, reason, dict(guard_info))
            effective_qty = Decimal(str(guard_info.get("effective_qty") or requested_qty))
            if guard_info.get("clamped"):
                logger.warning(
                    "SELL 守卫：approval=%s 请求 %s > 可卖 %s，按可卖量提交（部分成交语义）",
                    approval.request_id, requested_qty, guard_info.get("sellable_qty"),
                )

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
                qty=effective_qty,
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
        filled_total = Decimal("0")
        for result in results:
            if result.status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED) and result.filled_qty:
                filled_total += Decimal(str(result.filled_qty))
            position = self._execution_service.apply_fill(
                result,
                strategy_version_id=str(approval.strategy_version_id),
                market=approval.market,
                ts_code=approval.ts_code,
                side=approval.direction,
                # 归属链：approval.order_intent_id == plan.plan_id，写入 v2_position.trade_plan_id
                # 确保人工审批/API 执行路径与 _auto_execute 自动执行路径口径一致，不丢 plan 关联。
                trade_plan_id=approval.order_intent_id,
                user_strategy_id=approval.user_strategy_id,
                account_id=approval.account_id,
                # SELL 在成交入账层再执行一次守卫过滤（防御并发/漏判；绝不平当日 lot/跨账户）
                enforce_sell_guard=_sell_side,
                now=now,
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

        # 部分成交判定：broker 实际成交量小于请求量（守卫 clamp/部分成交/超持仓）→ PARTIALLY
        partially_filled = _sell_side and filled_total < requested_qty and filled_total > 0
        if over_sell_detected or partially_filled:
            # 超持仓/部分成交：已平部分不回滚，但标记为 PARTIALLY_EXECUTED 而非 EXECUTED，供人工核查
            logger.warning(
                "approval %s 超持仓/部分卖出（请求 %s，成交 %s，守卫 %s），标记 PARTIALLY_EXECUTED，请核查",
                approval.request_id, requested_qty, filled_total,
                "denied" if guard_info is None else {k: guard_info.get(k) for k in (
                    "sellable_qty", "t1_locked_qty", "ownership_excluded_qty")},
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
