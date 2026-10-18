"""实盘券商抽象占位。

真实实盘接入前，这里始终拒绝执行，确保不会误下真实订单。
"""
from __future__ import annotations

from zhice_os_v2.domain.execution.broker import (
    OrderRequest,
    OrderResult,
    OrderStatus,
)
from zhice_os_v2.domain.market.context import MarketContext


class LiveBroker:
    def __init__(self) -> None:
        self.enabled = False

    def place_order(self, ctx: MarketContext, request: OrderRequest) -> OrderResult:
        if not self.enabled:
            return OrderResult(
                order_id=request.order_id,
                status=OrderStatus.REJECTED,
                message="live_broker_disabled",
            )
        raise NotImplementedError("live broker adapter not implemented yet")

    def cancel_order(self, ctx: MarketContext, order_id: str) -> None:
        raise NotImplementedError("live broker adapter not implemented yet")

    def get_order_status(self, ctx: MarketContext, order_id: str) -> OrderStatus:
        return OrderStatus.REJECTED
