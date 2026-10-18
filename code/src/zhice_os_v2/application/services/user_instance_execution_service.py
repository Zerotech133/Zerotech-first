"""UserInstanceExecutionService：把共享候选 fan-out 到用户策略运行实例（Phase B Task 4）。

三层职责，边界清晰：
- ``materialize_plans``  纯函数：给一个共享候选 + 一组已启用实例，为每个实例产出一份带
  ``user_strategy_id``/``account_id`` 归属的 ``TradePlan``。plan_id 每实例唯一（fit_id 压
  candidate_id:user_strategy_id）。**绝不落库**。
- ``preview_fanout``    只读：加载该市场已启用实例，返回将产生多少 owner 计划/涉及多少实例、
  账户、策略版本（计数与指纹）。**绝不**落库/预留/通知——供 shadow 模式只算指标。
- ``execute_fanout``    enforced 路径：加载已启用实例 → 选股产出共享候选 → 每实例 materialize
  计划 → 经 T3 ``AccountLedgerService.reserve`` 预留预算 → 持久化带归属的计划。

Task 5 边界（务必遵守）：execute_fanout **只到"建带归属计划 + 预留"**。下游 approval/
execution/fill/position/replay 的归属贯穿（RuntimeContext + consume/release 预留）属 Task 5，
本服务刻意不触发审批/撮合/成交，见 execute_fanout 末尾注释。

legacy/shadow 均不经 execute_fanout（execute_fanout 仅 enforced 调用），故本服务的新增行为
在默认 legacy 下完全不参与执行链，安全。
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from zhice_os_v2.domain.candidate.candidate import Candidate
from zhice_os_v2.domain.ids import fit_id
from zhice_os_v2.domain.planning.trade_plan import TradePlan

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from zhice_os_v2.application.services.account_ledger_service import (
        AccountLedgerService,
    )
    from zhice_os_v2.application.services.opening_submission_guard import (
        OpeningSubmissionGuard,
    )
    from zhice_os_v2.application.services.user_strategy_service import (
        UserStrategyService,
    )
    from zhice_os_v2.domain.accounts.reservation import Reservation
    from zhice_os_v2.domain.approval.approval import ApprovalRequest
    from zhice_os_v2.domain.execution.runtime_context import RuntimeContext
    from zhice_os_v2.domain.portfolio.position import PositionLot
    from zhice_os_v2.domain.strategy.user_strategy import UserStrategyInstance
    from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FanoutPreview:
    """shadow 模式只读指纹：该市场 fan-out 将涉及的实例/账户/策略版本计数。"""

    market: str
    trade_date: str
    strategy_types: tuple[str, ...]
    instance_count: int
    account_count: int
    strategy_version_count: int

    def to_metrics(self) -> dict:
        return {
            "market": self.market,
            "trade_date": self.trade_date,
            "strategy_types": list(self.strategy_types),
            "instance_count": self.instance_count,
            "account_count": self.account_count,
            "strategy_version_count": self.strategy_version_count,
        }


@dataclass(frozen=True)
class OwnedTradeResult:
    """一条 owned 计划走完 enforced 下游流水后的结果（归属贯穿证据 + 预留终态）。"""

    plan: TradePlan
    status: str  # EXECUTED | REJECTED | NOT_FILLED
    risk_result: str
    approval: ApprovalRequest | None = None
    intent_id: str | None = None
    fill_id: str | None = None
    position: PositionLot | None = None
    reservation: Reservation | None = None


class UserInstanceExecutionService:
    def __init__(
        self,
        db: PersistencePort,
        *,
        user_strategy_service: UserStrategyService | None = None,
        ledger: AccountLedgerService | None = None,
        session_factory: Callable[[], Session] | None = None,
        opening_guard: OpeningSubmissionGuard | None = None,
    ) -> None:
        self._db = db
        self._user_strategies = user_strategy_service
        self._ledger = ledger
        # enforced owned 订单链落库用的会话工厂（默认真 get_session；测试注入隔离 sqlite）。
        self._session_factory = session_factory
        # B2-3 开仓提交门禁：owned BUY 在 broker 提交边界复核会员（撤会员的自建私有实例被拒）。
        # 默认惰性构造真 guard（读真表）；内存 db 测试注入放行桩，避免真 guard 读空表误拒。
        self._opening_guard = opening_guard

    def _session(self) -> Session:
        if self._session_factory is not None:
            return self._session_factory()
        from zhice_os_v2.infrastructure.db.session import get_session

        return get_session()

    # 依赖惰性构造：避免 __init__ 触发仓储/会话装配（materialize 纯函数场景零成本）。
    @property
    def user_strategies(self) -> UserStrategyService:
        if self._user_strategies is None:
            from zhice_os_v2.application.services.user_strategy_service import (
                UserStrategyService,
            )

            self._user_strategies = UserStrategyService(self._db)
        return self._user_strategies

    @property
    def ledger(self) -> AccountLedgerService:
        if self._ledger is None:
            from zhice_os_v2.application.services.account_ledger_service import (
                AccountLedgerService,
            )

            self._ledger = AccountLedgerService()
        return self._ledger

    @property
    def opening_guard(self) -> OpeningSubmissionGuard:
        if self._opening_guard is None:
            from zhice_os_v2.application.services.opening_submission_guard import (
                OpeningSubmissionGuard,
            )

            self._opening_guard = OpeningSubmissionGuard()
        return self._opening_guard

    def _opening_context(
        self, user_strategy_id: str | None
    ) -> AbstractContextManager[Any]:
        """B2-3 开仓门禁上下文：带归属 → guard.hold（提交边界复核会员，可抛
        OpeningSubmissionDenied）；无归属 → nullcontext（legacy/退出不经门禁）。
        两处提交点（run_owned_plan / workflow _auto_execute）共用同一语义，防未来分叉。"""
        if user_strategy_id:
            return self.opening_guard.hold(user_strategy_id=user_strategy_id)
        return nullcontext()

    # ── materialize（纯函数，不落库）─────────────────────────────────────────
    def materialize_plans(
        self,
        candidate: Candidate,
        instances: Sequence[UserStrategyInstance],
        *,
        ref_price: Decimal | None = None,
        qty: Decimal | None = None,
    ) -> list[TradePlan]:
        """一个共享候选 → 每个已启用实例一份带归属的 TradePlan。

        纯函数：不读写数据库、不预留。plan_id 用 fit_id 压
        ``{candidate_id}:{user_strategy_id}`` 保证每实例唯一（≤64）。空实例列表 → []。
        ref_price/qty 缺省为 0（fan-out 映射本身与定价解耦；enforced 路径由 execute_fanout
        传入真实定价与数量）。
        """
        rp = Decimal(str(ref_price)) if ref_price is not None else Decimal("0")
        q = Decimal(str(qty)) if qty is not None else Decimal("0")
        plans: list[TradePlan] = []
        for inst in instances:
            plan_id = fit_id(
                "plan-", f"{candidate.candidate_id}:{inst.user_strategy_id}"
            )
            plans.append(
                TradePlan(
                    plan_id=plan_id,
                    strategy_version_id=candidate.strategy_version_id,
                    market=candidate.market,
                    ts_code=candidate.ts_code,
                    direction="BUY",
                    qty=q,
                    ref_price=rp,
                    user_strategy_id=inst.user_strategy_id,
                    account_id=inst.account_id,
                )
            )
        return plans

    # ── preview（只读，供 shadow）────────────────────────────────────────────
    def preview_fanout(
        self, market: str, trade_date: date, strategy_types: Sequence[str]
    ) -> FanoutPreview:
        """只读：统计该市场已启用实例的 fan-out 形状（实例/账户/策略版本计数）。

        绝不落库、绝不预留、绝不发通知。供 shadow 模式只算指标。
        """
        instances = self.user_strategies.list_enabled_for_market(market)
        account_ids = {i.account_id for i in instances}
        sv_ids = {i.strategy_version_id for i in instances}
        return FanoutPreview(
            market=market,
            trade_date=str(trade_date),
            strategy_types=tuple(strategy_types),
            instance_count=len(instances),
            account_count=len(account_ids),
            strategy_version_count=len(sv_ids),
        )

    # ── execute（enforced，只到"建带归属计划 + 预留"）─────────────────────────
    def execute_fanout(
        self, market: str, trade_date: date, strategy_types: Sequence[str]
    ) -> dict:
        """enforced：加载已启用实例 → 选股共享候选 → 每实例建带归属计划 + 资金预留。

        实例按 ``strategy_version_id`` 与候选匹配（候选 sv 与实例 sv 同源）。某实例预留失败
        （现金/配额不足等）→ 跳过该实例并记录，不影响其它实例。
        """
        # 延迟导入：避免应用层 import 环（workflow_service 亦仅在其 run_pre_market 内惰性
        # 引入本服务）。
        from zhice_os_v2.api.routers.positions import _get_latest_prices
        from zhice_os_v2.application.services.position_sizing_service import (
            size_new_position,
        )
        from zhice_os_v2.application.services.strategy_registry_service import (
            StrategyRegistryService,
        )
        from zhice_os_v2.application.services.workflow_service import (
            _make_candidate_id,
            _sv_for_type,
        )

        instances = self.user_strategies.list_enabled_for_market(market)
        if not instances:
            return {
                "market": market,
                "trade_date": str(trade_date),
                "mode": "enforced",
                "instances": 0,
                "plans": [],
                "reservations": [],
                "skipped": [],
            }

        by_sv: dict[str, list[UserStrategyInstance]] = {}
        for inst in instances:
            by_sv.setdefault(inst.strategy_version_id, []).append(inst)

        from zhice_os_v2.domain.execution.runtime_context import RuntimeContext

        registry = StrategyRegistryService()
        built_plans: list[str] = []
        reservations: list[str] = []
        results: list[dict] = []
        skipped: list[dict] = []

        for st in strategy_types:
            sv = _sv_for_type(market, st)
            if not sv:
                continue
            matched = by_sv.get(sv, [])
            if not matched:
                continue
            try:
                picks = registry.run(st, trade_date, market=market)
            except KeyError:
                logger.warning("execute_fanout unknown strategy_type=%s, skipped", st)
                continue
            codes = [c["ts_code"] for c in picks]
            prices = _get_latest_prices(codes, market) if codes else {}
            for c in picks:
                px = prices.get(c["ts_code"])
                ref_price = (
                    Decimal(str(px)) if px and px > 0 else Decimal("100")
                )
                candidate = Candidate(
                    candidate_id=_make_candidate_id(sv, c["ts_code"], trade_date),
                    strategy_version_id=sv,
                    market=market,
                    ts_code=c["ts_code"],
                    score=float(c.get("score", 0)),
                    reason=c.get("reason", ""),
                )
                for inst in matched:
                    qty = size_new_position(
                        self._db, market, sv, c["ts_code"], ref_price
                    )
                    if float(qty) <= 0:
                        continue  # 预算满/超上限：跳过该实例（非异常）
                    plan = self.materialize_plans(
                        candidate, [inst], ref_price=ref_price, qty=Decimal(qty)
                    )[0]
                    amount = plan.ref_price * plan.qty
                    # T3 资金预留（单事务行锁 + 幂等 key=plan_id）。失败 → 跳过该实例。
                    try:
                        reservation = self.ledger.reserve(
                            inst.account_id,
                            inst.user_strategy_id,
                            amount,
                            plan.plan_id,
                        )
                    except Exception as exc:  # noqa: BLE001 - 单实例预留失败不拖垮其它
                        logger.info(
                            "execute_fanout reserve skipped instance=%s ts=%s: %s",
                            inst.user_strategy_id, c["ts_code"], exc,
                        )
                        skipped.append(
                            {
                                "user_strategy_id": inst.user_strategy_id,
                                "ts_code": c["ts_code"],
                                "reason": str(exc),
                            }
                        )
                        continue
                    built_plans.append(plan.plan_id)
                    reservations.append(plan.plan_id)
                    # ── 单链（[C1+C2]）：预留后立刻走全生命周期 run_owned_plan，
                    # 不再"只 reserve 不结算"（C1 冻结孤儿），且同一 plan 只预留这一次
                    # （C2 双预留消除——run_owned_plan 接收既有 reservation_id、不重复预留）。
                    ctx = RuntimeContext.from_instance(inst, execution_mode="enforced")
                    try:
                        outcome = self.run_owned_plan(
                            plan, ctx, reservation.reservation_id
                        )
                        results.append(
                            {
                                "plan_id": plan.plan_id,
                                "user_strategy_id": inst.user_strategy_id,
                                "ts_code": c["ts_code"],
                                "status": outcome.status,
                            }
                        )
                    except Exception as exc:  # noqa: BLE001 - 单实例下游失败不拖垮其它
                        # run_owned_plan 内部 try/finally 已保证：未成交预留必 release、
                        # 已成交则保守冻结 RESERVED（不少扣）。此处仅记录不拖垮其它实例。
                        logger.exception(
                            "execute_fanout owned pipeline failed instance=%s ts=%s: %s",
                            inst.user_strategy_id, c["ts_code"], exc,
                        )
                        results.append(
                            {
                                "plan_id": plan.plan_id,
                                "user_strategy_id": inst.user_strategy_id,
                                "ts_code": c["ts_code"],
                                "status": "ERROR",
                            }
                        )

        # 单链：每个建好的带归属计划都已走完 risk→approval→execution→fill→
        # consume/release 全生命周期。不存在只 reserve 不结算的出口（[C1]）。
        return {
            "market": market,
            "trade_date": str(trade_date),
            "mode": "enforced",
            "instances": len(instances),
            "plans": built_plans,
            "reservations": reservations,
            "results": results,
            "skipped": skipped,
        }

    # ── enforced owned 下游流水（Task 5：贯穿归属 + 预留 consume/release）──────────
    def run_owned_trade(
        self,
        instance: UserStrategyInstance,
        *,
        market: str,
        ts_code: str,
        ref_price: Decimal,
        qty: Decimal,
    ) -> OwnedTradeResult:
        """enforced 单实例便捷入口：为一个实例建带归属计划 + 预留一次 → 跑 owned 下游流水。

        归属权威取自 instance（RuntimeContext.from_instance）。预留 idempotency_key=plan_id。
        **单链保证**：本入口自己 reserve **一次**，随即 run_owned_plan（其接收该
        reservation_id、不重复预留）完成 consume/release——不存在只 reserve 不结算的出口，
        也不与 execute_fanout 对同一计划二次预留（生产盘前走 execute_fanout 单链，本入口
        供单实例手动触发/测试，两者各自单次预留，互不叠加）。
        """
        from zhice_os_v2.domain.execution.runtime_context import RuntimeContext

        ctx = RuntimeContext.from_instance(instance, execution_mode="enforced")
        candidate = Candidate(
            candidate_id=fit_id("cand-", f"{instance.strategy_version_id}:{ts_code}"),
            strategy_version_id=instance.strategy_version_id,
            market=market,
            ts_code=ts_code,
            score=0.0,
            reason="owned_trade",
        )
        plan = self.materialize_plans(
            candidate,
            [instance],
            ref_price=Decimal(str(ref_price)),
            qty=Decimal(str(qty)),
        )[0]
        amount = plan.ref_price * plan.qty
        reservation = self.ledger.reserve(
            instance.account_id, instance.user_strategy_id, amount, plan.plan_id
        )
        return self.run_owned_plan(plan, ctx, reservation.reservation_id)

    def run_owned_plan(
        self,
        plan: TradePlan,
        ctx: RuntimeContext,
        reservation_id: str,
    ) -> OwnedTradeResult:
        """把一条 owned 计划贯穿 risk→approval→execution→fill→position，并结算预留。

        归属（ctx.user_strategy_id/account_id）贯穿到 plan/approval/order_intent/position。
        预留结算保证（T3 复核 #4 交办的"保证释放路径"）：
        - 风控 BLOCK / 未成交 → release（幂等，不实扣，不泄漏冻结资金）；
        - 成交成功 → consume（reserved_cash -= amount 且 cash_balance -= amount）；
        - 任意下游异常 → finally 中 release（幂等），异常继续上抛。
        用 ``settled`` 标记避免对已 consume/release 的预留重复结算。
        """
        from zhice_os_v2.api.dependencies import get_switches
        from zhice_os_v2.application.services.approval_center import ApprovalCenter
        from zhice_os_v2.application.services.execution_service import ExecutionService
        from zhice_os_v2.application.services.opening_submission_guard import (
            OpeningSubmissionDenied,
        )
        from zhice_os_v2.application.services.risk_service import RiskService
        from zhice_os_v2.application.services.workflow_service import _sim_auto
        from zhice_os_v2.domain.approval.approval import ApprovalStatus
        from zhice_os_v2.domain.execution.broker import (
            OrderRequest,
            OrderSide,
        )
        from zhice_os_v2.domain.market.context import MarketContext
        from zhice_os_v2.domain.market.registry import get_profile
        from zhice_os_v2.domain.risk.risk import RiskResult
        from zhice_os_v2.infrastructure.brokers.simulated import SimulatedBroker

        settled = False  # 预留已 consume/release 结算
        filled = False  # 持仓已落库（成交）；一旦 True 禁止 release（否则少扣造钱）
        try:
            # 0. owned SELL fail-closed（[必修 #3]）：_apply_sell 按 strategy_version_id+
            # ts_code FIFO 平仓、无 owner 过滤——owned 路径若发 SELL 会跨 owner 平掉他人
            # 同版本持仓且更新无归属。owned 目前 BUY-only，非买入方向一律拒绝、不下单、
            # 不进入 _apply_sell，释放预留。完整 owned 卖出（owner 过滤 FIFO）为后续任务。
            if str(getattr(plan.direction, "value", plan.direction)).upper() != "BUY":
                self.ledger.release(reservation_id)
                settled = True
                raise ValueError("owned_sell_not_supported")

            # 归属已在 plan 上（materialize/调用方保证）；持久化带归属计划。
            self._db.save_trade_plan(plan)

            # 1. 风控。
            risk = RiskService().check_plan(plan)
            if risk.result == RiskResult.BLOCK:
                self.ledger.release(reservation_id)
                settled = True
                return OwnedTradeResult(
                    plan=plan,
                    status="REJECTED",
                    risk_result=risk.result.value,
                    reservation=self.ledger.get_reservation(reservation_id),
                )

            # 2. 授权（继承归属）。ApprovalCenter 用 get_switches()（与 legacy 一致的治理
            # StrategySwitch 源，延迟导入避免环）——live_auto_follow 等治理开关照旧生效。
            approval = ApprovalCenter(get_switches()).create_approval(
                market=plan.market,
                strategy_version_id=plan.strategy_version_id,
                order_intent_id=plan.plan_id,
                ts_code=plan.ts_code,
                direction=plan.direction,
                qty=float(plan.qty),
                price=float(plan.ref_price),
                reason="owned_enforced",
            )
            approval.user_strategy_id = ctx.user_strategy_id
            approval.account_id = ctx.account_id
            # enforced 自动执行须**硬 AND** 市场级 kill-switch（[必修 #2]）+ 风控 PASS 门
            # （[I3]，对齐 legacy workflow_service:843 "仅 PASS 自动批准，WARN 强制人工"）：
            # 实例 auto_execute=True 且市场 _sim_auto 开放且 risk==PASS 才自动批准；
            # 市场停牌或风控 WARN → 停 PENDING（下方状态门 release 预留、返回 PENDING）。
            if (
                ctx.auto_execute
                and _sim_auto(plan.market)
                and risk.result == RiskResult.PASS
                and approval.status == ApprovalStatus.PENDING
            ):
                approval.approve(actor="enforced")
            self._db.save_approval(approval)

            # 2b. 审批状态门控（[必修 #2]）：只有 APPROVED 才继续成交。
            # auto_execute=False → 停 PENDING（等人工），或被拒 → 不下单、不成交，
            # 释放预留（无持仓，release 正确），杜绝绕过人工审批闸成交。
            if approval.status != ApprovalStatus.APPROVED:
                self.ledger.release(reservation_id)
                settled = True
                status = (
                    "PENDING"
                    if approval.status == ApprovalStatus.PENDING
                    else "REJECTED"
                )
                return OwnedTradeResult(
                    plan=plan,
                    status=status,
                    risk_result=risk.result.value,
                    approval=approval,
                    reservation=self.ledger.get_reservation(reservation_id),
                )

            # 2c. 熔断急停 fail-closed（[I1]，对齐 legacy _auto_execute:928）：熔断激活或
            # 检查失败 → 不成交、release 预留、返回 HALTED。已批准但未成交，release 正确。
            try:
                from zhice_os_v2.api.routers.circuit_breaker import is_tripped

                tripped = is_tripped()
            except Exception as exc:  # noqa: BLE001 - 熔断检查失败按 fail-closed 停手
                logger.error(
                    "owned circuit breaker 检查失败，fail-closed 停手 plan=%s: %s",
                    plan.plan_id, exc,
                )
                tripped = True
            if tripped:
                logger.warning(
                    "owned circuit breaker active, skip execute plan=%s ts=%s",
                    plan.plan_id, plan.ts_code,
                )
                self.ledger.release(reservation_id)
                settled = True
                return OwnedTradeResult(
                    plan=plan,
                    status="HALTED",
                    risk_result=risk.result.value,
                    approval=approval,
                    reservation=self.ledger.get_reservation(reservation_id),
                )

            # 2d. 离群价护栏（[I2]，复用 legacy WorkflowService._outlier_price_ok）：撮合
            # 基准价（plan.ref_price）偏离昨收 >阈值 → 判脏价/symbol↔price 错位，拒绝建仓、
            # release 预留、返回 REJECTED，绝不按脏价成交。
            from zhice_os_v2.application.services.workflow_service import WorkflowService

            if not WorkflowService(self._db)._outlier_price_ok(
                plan.ref_price, plan.ts_code, plan.market
            ):
                self.ledger.release(reservation_id)
                settled = True
                return OwnedTradeResult(
                    plan=plan,
                    status="REJECTED",
                    risk_result=risk.result.value,
                    approval=approval,
                    reservation=self.ledger.get_reservation(reservation_id),
                )

            # 3. 执行（模拟撮合；实盘门由上游 execution_mode 控制，本任务用模拟）。
            profile = get_profile(plan.market)
            mctx = MarketContext(
                market=plan.market,
                profile=profile,
                currency=profile.currency,
                tz=profile.tz,
            )
            broker = SimulatedBroker()
            order_req = OrderRequest(
                order_id=f"{plan.plan_id}-sim",
                market=plan.market,
                ts_code=plan.ts_code,
                side=OrderSide(plan.direction),
                qty=plan.qty,
                limit_price=plan.ref_price,
            )
            # B2-3 开仓提交门禁：owned BUY 在提交边界复核会员，持锁窗口内与撤会员事务
            # 线性化。撤销会员用户的自建私有实例在此被拒（OpeningSubmissionDenied）：
            # 释放预留、settled=True、不成交（filled 仍 False，release 安全）、返回
            # ENTITLEMENT_DENIED。无归属（ctx.user_strategy_id 空）不经门禁。
            try:
                with self._opening_context(ctx.user_strategy_id):
                    result = broker.place_order(mctx, order_req)
            except OpeningSubmissionDenied as denied:
                logger.warning(
                    "owned 开仓门禁拒绝 plan=%s user_strategy=%s code=%s，释放预留、不成交",
                    plan.plan_id, ctx.user_strategy_id, denied.code,
                )
                self.ledger.release(reservation_id)
                settled = True
                return OwnedTradeResult(
                    plan=plan,
                    status="ENTITLEMENT_DENIED",
                    risk_result=risk.result.value,
                    approval=approval,
                    reservation=self.ledger.get_reservation(reservation_id),
                )

            # 4. 持仓（继承归属）。
            position = ExecutionService(self._db).apply_fill(
                result,
                strategy_version_id=plan.strategy_version_id,
                market=plan.market,
                ts_code=plan.ts_code,
                side=plan.direction,
                trade_plan_id=plan.plan_id,
                user_strategy_id=ctx.user_strategy_id,
                account_id=ctx.account_id,
            )
            if position is None:
                # 未成交（拒单/流动性不足等）：释放预留，不实扣。
                self.ledger.release(reservation_id)
                settled = True
                return OwnedTradeResult(
                    plan=plan,
                    status="NOT_FILLED",
                    risk_result=risk.result.value,
                    approval=approval,
                )

            # 成交已落库：一旦到此，禁止 release（release 会少扣 → 持仓存在但现金未扣造钱）。
            filled = True

            # 5. 订单链落库（intent 带归属；request/execution/fill 经链解析归属）。
            intent_id, fill_id = self._persist_owned_order_chain(plan, ctx, result)

            # 6. 成交成功 → 消费预留（实扣）。
            # [必修 #1] 按**实际成交额** consume（部分成交只扣 filled_price*filled_qty，
            # reserved 全额解冻），防满额超扣、账实不符。缺实际价量时回退预留满额。
            actual_price = result.filled_price or plan.ref_price
            actual_qty = result.filled_qty or plan.qty
            actual_amount = (actual_price or Decimal("0")) * (actual_qty or Decimal("0"))
            # [必修 #1] consume 在 fill/持仓已 commit 后失败：不 release（少扣造钱），
            # 保守冻结在 RESERVED 待对账，记 error（呼应 T3 复核 #4 reconciliation），异常上抛。
            try:
                self.ledger.consume(reservation_id, actual_amount=actual_amount)
            except Exception:
                logger.error(
                    "run_owned_plan consume 失败但持仓已落库，保守冻结 RESERVED 待对账："
                    "plan=%s reservation=%s account=%s user_strategy=%s position=%s",
                    plan.plan_id,
                    reservation_id,
                    ctx.account_id,
                    ctx.user_strategy_id,
                    position.position_id,
                )
                settled = True  # 阻止 finally 兜底 release（filled 已 True 亦不 release）
                raise
            settled = True
            return OwnedTradeResult(
                plan=plan,
                status="EXECUTED",
                risk_result=risk.result.value,
                approval=approval,
                intent_id=intent_id,
                fill_id=fill_id,
                position=position,
                reservation=self.ledger.get_reservation(reservation_id),
            )
        finally:
            # 保证释放路径：未结算（异常中断）且**未成交**的预留必被 release，杜绝冻结资金泄漏。
            # 已成交（filled）绝不 release——保守冻结优先于少扣造钱（[必修 #1]）。
            if not settled and not filled:
                try:
                    self.ledger.release(reservation_id)
                except Exception:  # noqa: BLE001 - 释放兜底不得掩盖原始异常
                    logger.exception(
                        "run_owned_plan 预留释放兜底失败 reservation=%s", reservation_id
                    )

    def _persist_owned_order_chain(
        self,
        plan: TradePlan,
        ctx: RuntimeContext,
        result,
    ) -> tuple[str, str]:
        """把 owned 成交落成 intent→request→execution→fill 四表（供 OwnershipResolver 解析）。

        仅 intent 带 user_strategy_id/account_id 归属（Task 1 只在 intent 建归属列）；
        request/execution/fill 经链解析。id 用 fit_id 压 ≤64；幂等 upsert（重复计划不重复写）。
        返回 (intent_id, fill_id)。
        """
        from datetime import UTC, datetime

        from zhice_os_v2.infrastructure.db import models as orm

        intent_id = fit_id("intent-", plan.plan_id)
        request_id = fit_id("req-", plan.plan_id)
        execution_id = fit_id("exec-", plan.plan_id)
        fill_id = fit_id("fill-", plan.plan_id)
        now = datetime.now(UTC)
        price = result.filled_price or plan.ref_price
        qty = result.filled_qty or plan.qty
        amount = (price or Decimal("0")) * (qty or Decimal("0"))

        session = self._session()
        try:
            def _get(model, pk_col, pk_val):
                return (
                    session.query(model).filter(pk_col == pk_val).first()
                )

            if _get(orm.OrderIntentModel, orm.OrderIntentModel.intent_id, intent_id) is None:
                session.add(orm.OrderIntentModel(
                    intent_id=intent_id,
                    plan_id=plan.plan_id,
                    strategy_version_id=plan.strategy_version_id,
                    market=plan.market,
                    ts_code=plan.ts_code,
                    intent_type="OPEN",
                    intent_side=plan.direction,
                    intent_qty=qty,
                    intent_price=price,
                    status="FILLED",
                    created_at=now,
                    # 运行时归属（enforced 路径贯穿）。
                    user_strategy_id=ctx.user_strategy_id,
                    account_id=ctx.account_id,
                ))
            if _get(
                orm.OrderRequestModel, orm.OrderRequestModel.request_id, request_id
            ) is None:
                session.add(orm.OrderRequestModel(
                    request_id=request_id,
                    intent_id=intent_id,
                    market=plan.market,
                    broker_channel="SIMULATED",
                    idempotency_key=plan.plan_id,
                    status="SUBMITTED",
                    created_at=now,
                ))
            if _get(
                orm.ExecutionRecordModel,
                orm.ExecutionRecordModel.execution_id,
                execution_id,
            ) is None:
                session.add(orm.ExecutionRecordModel(
                    execution_id=execution_id,
                    request_id=request_id,
                    market=plan.market,
                    broker_order_id=f"SIM-{plan.plan_id}",
                    execution_status="FILLED",
                    created_at=now,
                ))
            if _get(orm.FillRecordModel, orm.FillRecordModel.fill_id, fill_id) is None:
                session.add(orm.FillRecordModel(
                    fill_id=fill_id,
                    execution_id=execution_id,
                    market=plan.market,
                    ts_code=plan.ts_code,
                    broker_trade_id=f"SIM-{plan.plan_id}",
                    fill_price=price,
                    fill_qty=qty,
                    fill_amount=amount,
                    fill_time=now,
                    created_at=now,
                ))
            session.commit()
        finally:
            session.close()
        return intent_id, fill_id
