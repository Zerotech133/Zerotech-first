"""执行编排：同一交易意图按开关并行执行模拟/实盘。

B2-3 接入两道权益门禁（仅侵入 broker 提交边界，不改既有开关/撮合语义）：
- 开仓提交门禁（OpeningSubmissionGuard）：当 ``side==BUY`` 且带 ``user_strategy_id`` 时，
  用 ``guard.hold(...)`` 包住**整个 sim+live 提交段**——持锁窗口内与撤会员事务线性化，
  撤销会员用户的自建私有实例在此被拒（OpeningSubmissionDenied），sim/live 都不提交。
  SELL/退出与 legacy（无 user_strategy_id）路径不经门禁。
- LIVE fail-closed：实盘 place_order 前，若无可靠外部撤单出箱
  （``cancellation_outbox.supports_durable_outbox`` False）→ 抛
  ``LiveExecutionNotConfigured``（code=broker.external_cancellation_not_configured）。
  撤会员时无法取消已挂实盘单 = 风险敞口，故没有出箱前禁止一切实盘。模拟盘不受影响。

guard/outbox 均可注入（默认真 guard + 模拟盘出箱），既有测试用内存 DB 时注入放行桩，
避免真 guard 经 get_session 读真表而误拒——不削弱真 guard。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from zhice_os_v2.application.services.approval_center import StrategySwitch
from zhice_os_v2.domain.execution.broker import (
    BrokerPort,
    ExecutionMode,
    OrderRequest,
    OrderSide,
)
from zhice_os_v2.domain.market.context import MarketContext
from zhice_os_v2.infrastructure.repositories.entitlement_risk_repository import (
    BrokerCancellationOutboxPort,
    SimulatedOnlyBrokerCancellationOutbox,
)

if TYPE_CHECKING:
    from zhice_os_v2.application.services.opening_submission_guard import (
        OpeningSubmissionGuard,
    )


class LiveExecutionNotConfigured(RuntimeError):
    """实盘提交前置未满足：无可靠外部撤单出箱前禁止实盘（fail-closed）。

    code 为稳定机读原因串，供上游按 code 分支/告警。
    """

    def __init__(
        self, code: str = "broker.external_cancellation_not_configured"
    ) -> None:
        super().__init__(code)
        self.code = code


class ExecutionOrchestrator:
    def __init__(
        self,
        brokers: dict[ExecutionMode, BrokerPort],
        switches: dict[tuple[str, str], StrategySwitch],
        *,
        opening_guard: OpeningSubmissionGuard | None = None,
        cancellation_outbox: BrokerCancellationOutboxPort | None = None,
    ) -> None:
        self._brokers = brokers
        self._switches = switches
        # 可注入开仓门禁：默认惰性构造真 guard（生产读真表）；测试可注入放行桩/隔离库 guard。
        self._opening_guard = opening_guard
        # 外部券商撤单出箱：默认模拟盘出箱（supports_durable_outbox=False）→ 实盘 fail-closed。
        self._cancellation_outbox = (
            cancellation_outbox or SimulatedOnlyBrokerCancellationOutbox()
        )

    @property
    def opening_guard(self) -> OpeningSubmissionGuard:
        if self._opening_guard is None:
            from zhice_os_v2.application.services.opening_submission_guard import (
                OpeningSubmissionGuard,
            )

            self._opening_guard = OpeningSubmissionGuard()
        return self._opening_guard

    def execute(
        self,
        ctx: MarketContext,
        strategy_version_id,
        order_intent_id: str,
        ts_code: str,
        side: OrderSide,
        qty,
        price,
        user_strategy_id: str | None = None,
    ) -> list:
        # 开关解析：先按真实策略号精确匹配；查不到回落到市场级通配开关 (market, 0)
        # （_SIM_SV=0，盘前内置授权与「模拟盘总开关」都写这个键——此前只精确查、
        # 真实策略号为字符串永远匹配不到 0，导致市场级开关形同虚设）。
        switch = (
            self._switches.get((ctx.market, strategy_version_id))
            or self._switches.get((ctx.market, "0"))
            or self._switches.get((ctx.market, 0))  # type: ignore[arg-type]
            or StrategySwitch(market=ctx.market, strategy_version_id=strategy_version_id)
        )

        def _submit() -> list:
            # LIVE fail-closed 前置到任何 place_order 之前：sim+live 同开且无可靠外部撤单
            # 出箱时，若先下 sim 单再抛异常会丢弃 sim fill。故在提交任何订单前判定——
            # 无出箱则整笔（含 sim）都不提交（撤会员无法撤已挂实盘单 = 敞口）。
            if (
                switch.live_enabled
                and self._brokers.get(ExecutionMode.LIVE) is not None
                and not self._cancellation_outbox.supports_durable_outbox
            ):
                raise LiveExecutionNotConfigured()
            results: list = []
            if switch.simulated_enabled:
                broker = self._brokers[ExecutionMode.SIMULATED]
                results.append(
                    broker.place_order(
                        ctx,
                        OrderRequest(
                            order_id=f"{order_intent_id}-sim",
                            market=ctx.market,
                            ts_code=ts_code,
                            side=side,
                            qty=qty,
                            limit_price=price,
                            execution_mode=ExecutionMode.SIMULATED,
                            strategy_version_id=strategy_version_id,
                            approval_request_id=order_intent_id,
                        ),
                    )
                )
            if switch.live_enabled:
                live_broker = self._brokers.get(ExecutionMode.LIVE)
                if live_broker is not None:
                    # LIVE fail-closed 已在 _submit 顶部前置判定（sim 之前）。此处直接下单。
                    results.append(
                        live_broker.place_order(
                            ctx,
                            OrderRequest(
                                order_id=f"{order_intent_id}-live",
                                market=ctx.market,
                                ts_code=ts_code,
                                side=side,
                                qty=qty,
                                limit_price=price,
                                execution_mode=ExecutionMode.LIVE,
                                strategy_version_id=strategy_version_id,
                                approval_request_id=order_intent_id,
                            ),
                        )
                    )
            return results

        # 开仓提交门禁：仅 BUY 且带归属（owned/enforced）才复核会员；持锁包住整个提交段，
        # 拒绝时 sim+live 都不提交（OpeningSubmissionDenied 上抛给调用方处理）。
        # SELL/退出与 legacy（user_strategy_id None）不经门禁。
        if side == OrderSide.BUY and user_strategy_id:
            with self.opening_guard.hold(user_strategy_id=user_strategy_id):
                return _submit()
        return _submit()
