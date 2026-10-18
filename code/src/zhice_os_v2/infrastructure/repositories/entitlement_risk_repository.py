"""撤权时停止"新增开仓风险"的清理仓储（B2-1）。

会员被撤销后，用户不应再产生**新的开仓**。本仓储在**调用方传入的同一 session/事务**内，
仅针对 target 用户自建私有策略的启用实例，做幂等的排队开仓清理：

  1. 停用这些实例（enabled→False）。
  2. 取消其下未成交的开仓交易计划（plan_status PENDING/APPROVED → CANCELLED）。
  3. 取消其下未领取执行的 BUY 审批（PENDING/APPROVED → CANCELLED；EXECUTING 让其完成）。
  4. 取消其下 OPEN-BUY-NEW 且**无** SUBMITTED/SENT OrderRequest 的意图（→ CANCELLED）。
  5. 已提交 request（本任务不动）→ cancelled_request_ids 恒为 ()。
  6. 释放被取消计划对应的 RESERVED 预留：账户 reserved_cash -= amount（cash_balance 不变），
     预留 → RELEASED。reserved_cash 将变负即抛错整体回滚（账实不符）。
  7. 外部券商挂单撤单入队（模拟盘无外部挂单，返回 ()）。

**不 commit、不 close**：仅操作传入 session；事务边界由调用方（AdminEntitlementService._run）持有。
不触碰既有持仓、已成交记录、已提交 request、平台公共策略实例、他人私有策略实例、CLOSE/SELL 方向。

锁序（严格，防死锁）：operation→user→membership（调用方已持有）→instances(id ASC,FOR UPDATE)
→accounts(account_id ASC,FOR UPDATE)→plans→approvals→intents→reservations→audit。
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy.orm import Session

from zhice_os_v2.infrastructure.db import models as orm


@dataclass(frozen=True)
class OpeningRiskCleanupResult:
    """一次撤权开仓清理的摘要（供审计与响应体投影）。"""

    disabled_instance_ids: Sequence[str]
    cancelled_plan_ids: Sequence[str]
    cancelled_approval_ids: Sequence[str]
    cancelled_intent_ids: Sequence[str]
    cancelled_request_ids: Sequence[str]  # 本任务恒为 ()（不取消已提交 request）
    released_reservation_ids: Sequence[str]
    released_amount: Decimal
    pending_external_cancellations: Sequence[str]


class BrokerCancellationOutboxPort(Protocol):
    """外部券商撤单出箱端口：在同事务内为待撤外部挂单入队（LIVE 由 B2-3 接线）。"""

    supports_durable_outbox: bool

    def enqueue_in_session(
        self,
        session: Session,
        *,
        target_user_id: str,
        user_strategy_ids: Sequence[str],
        now: datetime,
    ) -> Sequence[str]:
        ...


class SimulatedOnlyBrokerCancellationOutbox:
    """模拟盘出箱：无外部券商挂单可撤，入队恒返回 ()（不落库）。"""

    supports_durable_outbox = False

    def enqueue_in_session(
        self,
        session: Session,
        *,
        target_user_id: str,
        user_strategy_ids: Sequence[str],
        now: datetime,
    ) -> Sequence[str]:
        return ()


# 状态字面量（与执行链一致；本任务清理路径的谓词权威）。
_PLAN_OPEN_STATUSES = ("PENDING", "APPROVED")
# 只取消尚未领取执行的审批（PENDING/APPROVED）。**不含 EXECUTING**：EXECUTING 表示
# 某执行路径已抢在撤权前（会员当时仍有效、开仓门禁放行）领取并正在提交 broker，
# 让其自然完成到 EXECUTED，避免撤权把"已成交交易"的审批错置为 CANCELLED（终审
# Important：mark_approval_executed 与本取消的跨事务竞态会致审计状态失真）。实例停用
# + 开仓门禁已阻止后续新开仓；预留释放按 plan 键、不依赖审批状态，故不受影响。
_APPROVAL_OPEN_STATUSES = ("PENDING", "APPROVED")
_REQUEST_SUBMITTED_STATUSES = ("SUBMITTED", "SENT")
_CANCELLED = "CANCELLED"
_RESERVED = "RESERVED"
_RELEASED = "RELEASED"


class EntitlementRiskRepository:
    """撤权开仓风险清理（无状态；每方法操作传入 session，不自开事务）。"""

    def stop_new_opening_risk(
        self,
        session: Session,
        *,
        actor_user_id: str,
        target_user_id: str,
        now: datetime,
        cancellation_outbox: BrokerCancellationOutboxPort,
    ) -> OpeningRiskCleanupResult:
        # EntitlementError 定义在 service；延迟导入避免模块级循环依赖。
        from zhice_os_v2.application.services.admin_entitlement_service import (
            EntitlementError,
        )

        # ── 1. 自建私有 + 启用实例（owner==target 且底层 Strategy PRIVATE 自建）──
        # 可见性/归属解析链：instance.strategy_version_id → StrategyVersion.strategy_id
        # → Strategy(owner_user_id==target AND visibility=='PRIVATE')。实例表无这两列。
        inst_rows = (
            session.query(orm.UserStrategyInstanceModel)
            .join(
                orm.StrategyVersionModel,
                orm.UserStrategyInstanceModel.strategy_version_id
                == orm.StrategyVersionModel.strategy_version_id,
            )
            .join(
                orm.StrategyModel,
                orm.StrategyVersionModel.strategy_id == orm.StrategyModel.strategy_id,
            )
            .filter(
                orm.UserStrategyInstanceModel.user_id == target_user_id,
                orm.UserStrategyInstanceModel.enabled.is_(True),
                orm.StrategyModel.owner_user_id == target_user_id,
                orm.StrategyModel.visibility == "PRIVATE",
            )
            .order_by(orm.UserStrategyInstanceModel.id.asc())
            .with_for_update(of=orm.UserStrategyInstanceModel)
            .all()
        )
        disabled_instance_ids: list[str] = []
        for inst in inst_rows:
            inst.enabled = False
            inst.updated_at = now
            disabled_instance_ids.append(inst.user_strategy_id)

        # ── 2. 账户行锁（account_id ASC）：覆盖后续待释放预留所属账户 ──
        # reserve() 保证 reservation.account_id == instance.account_id，故被停实例账户集
        # 即待释放预留账户全集；先锁定，锁序稳定防死锁。
        account_ids = sorted({inst.account_id for inst in inst_rows})
        accounts: dict[str, orm.UserAccountModel] = {}
        if account_ids:
            for acct in (
                session.query(orm.UserAccountModel)
                .filter(orm.UserAccountModel.account_id.in_(account_ids))
                .order_by(orm.UserAccountModel.account_id.asc())
                .with_for_update()
                .all()
            ):
                accounts[acct.account_id] = acct

        # ── 3. 取消未成交开仓计划（PENDING/APPROVED → CANCELLED）──
        cancelled_plan_ids: list[str] = []
        if disabled_instance_ids:
            plan_rows = (
                session.query(orm.TradePlanModel)
                .filter(
                    orm.TradePlanModel.user_strategy_id.in_(disabled_instance_ids),
                    orm.TradePlanModel.plan_status.in_(_PLAN_OPEN_STATUSES),
                )
                .order_by(orm.TradePlanModel.id.asc())
                .with_for_update()
                .all()
            )
            for plan in plan_rows:
                plan.plan_status = _CANCELLED
                cancelled_plan_ids.append(plan.plan_id)

        # ── 4. 取消未领取执行的 BUY 审批（PENDING/APPROVED → CANCELLED）──
        cancelled_approval_ids: list[str] = []
        if disabled_instance_ids:
            appr_rows = (
                session.query(orm.ApprovalRequestModel)
                .filter(
                    orm.ApprovalRequestModel.user_strategy_id.in_(disabled_instance_ids),
                    orm.ApprovalRequestModel.direction == "BUY",
                    orm.ApprovalRequestModel.status.in_(_APPROVAL_OPEN_STATUSES),
                )
                .order_by(orm.ApprovalRequestModel.id.asc())
                .with_for_update()
                .all()
            )
            for appr in appr_rows:
                appr.status = _CANCELLED
                appr.decided_at = now
                appr.decided_by = actor_user_id
                appr.reject_reason = "entitlements.membership_revoked"
                cancelled_approval_ids.append(appr.request_id)

        # ── 5. 取消 OPEN-BUY-NEW 且无 SUBMITTED/SENT request 的意图 ──
        cancelled_intent_ids: list[str] = []
        if disabled_instance_ids:
            intent_rows = (
                session.query(orm.OrderIntentModel)
                .filter(
                    orm.OrderIntentModel.user_strategy_id.in_(disabled_instance_ids),
                    orm.OrderIntentModel.intent_type == "OPEN",
                    orm.OrderIntentModel.intent_side == "BUY",
                    orm.OrderIntentModel.status == "NEW",
                )
                .order_by(orm.OrderIntentModel.id.asc())
                .with_for_update()
                .all()
            )
            candidate_intent_ids = [i.intent_id for i in intent_rows]
            submitted_intent_ids: set[str] = set()
            if candidate_intent_ids:
                # 无锁读安全：候选 intent 行已 FOR UPDATE 锁定，且其所属实例本方法开头已
                # 置 disabled——不会再有新 request 为这些 intent 提交（提交路径受 B2 guard
                # 拦截 disabled 实例）。故"是否已提交"的判定在本事务内稳定，无需锁 request。
                for (iid,) in (
                    session.query(orm.OrderRequestModel.intent_id)
                    .filter(
                        orm.OrderRequestModel.intent_id.in_(candidate_intent_ids),
                        orm.OrderRequestModel.status.in_(_REQUEST_SUBMITTED_STATUSES),
                    )
                    .all()
                ):
                    submitted_intent_ids.add(iid)
            for intent in intent_rows:
                # 已提交 request 的 intent 不取消（B2 后续按 request 边界处理）。
                if intent.intent_id in submitted_intent_ids:
                    continue
                intent.status = _CANCELLED
                cancelled_intent_ids.append(intent.intent_id)

        # ── 6. 释放被取消计划对应的 RESERVED 预留（reserved_cash -= amount）──
        released_reservation_ids: list[str] = []
        released_amount = Decimal("0")
        if cancelled_plan_ids:
            resv_rows = (
                session.query(orm.AccountReservationModel)
                .filter(
                    # 现路径 reservation.idempotency_key == plan.plan_id。
                    orm.AccountReservationModel.idempotency_key.in_(cancelled_plan_ids),
                    orm.AccountReservationModel.status == _RESERVED,
                )
                .order_by(orm.AccountReservationModel.id.asc())
                .with_for_update()
                .all()
            )
            for resv in resv_rows:
                resv_acct = accounts.get(resv.account_id)
                if resv_acct is None:
                    # 理论不可达（预留账户 ⊆ 被停实例账户）；防御性锁定，保持行为正确。
                    resv_acct = (
                        session.query(orm.UserAccountModel)
                        .filter_by(account_id=resv.account_id)
                        .with_for_update()
                        .one_or_none()
                    )
                    if resv_acct is None:
                        raise EntitlementError(
                            "entitlements.reservation_balance_inconsistent"
                        )
                    accounts[resv.account_id] = resv_acct
                amount = Decimal(str(resv.amount))
                new_reserved = Decimal(str(resv_acct.reserved_cash)) - amount
                if new_reserved < 0:
                    # 账实不符（DB 约束 reserved_cash>=0 亦会拒）：整体回滚。
                    raise EntitlementError(
                        "entitlements.reservation_balance_inconsistent"
                    )
                resv_acct.reserved_cash = new_reserved
                resv_acct.updated_at = now
                resv.status = _RELEASED
                resv.updated_at = now
                released_amount += amount
                released_reservation_ids.append(resv.reservation_id)

        session.flush()

        # ── 7. 外部券商撤单入队（模拟盘 → ()）──
        pending_external = tuple(
            cancellation_outbox.enqueue_in_session(
                session,
                target_user_id=target_user_id,
                user_strategy_ids=tuple(disabled_instance_ids),
                now=now,
            )
        )

        return OpeningRiskCleanupResult(
            disabled_instance_ids=tuple(disabled_instance_ids),
            cancelled_plan_ids=tuple(cancelled_plan_ids),
            cancelled_approval_ids=tuple(cancelled_approval_ids),
            cancelled_intent_ids=tuple(cancelled_intent_ids),
            cancelled_request_ids=(),
            released_reservation_ids=tuple(released_reservation_ids),
            released_amount=released_amount,
            pending_external_cancellations=pending_external,
        )
