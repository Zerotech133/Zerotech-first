"""纸面交易券商：基于实时行情的纸面执行。"""
from __future__ import annotations

from decimal import Decimal

from zhice_os_v2.domain.execution.broker import (
    OrderRequest,
    OrderResult,
    OrderStatus,
)
from zhice_os_v2.domain.market.context import MarketContext
from zhice_os_v2.infrastructure.market_data.memory import MemoryMarketData


class PaperBroker:
    def __init__(self, market_data: MemoryMarketData) -> None:
        self._market_data = market_data

    def place_order(self, ctx: MarketContext, request: OrderRequest) -> OrderResult:
        quote = self._market_data.get_quote(request.ts_code)
        price = Decimal(str(quote.price)) if quote else request.limit_price or Decimal("0")
        return OrderResult(
            order_id=request.order_id,
            status=OrderStatus.FILLED,
            filled_qty=request.qty,
            filled_price=price,
            message="paper_filled",
        )

    def cancel_order(self, ctx: MarketContext, order_id: str) -> None:
        return None

    def get_order_status(self, ctx: MarketContext, order_id: str) -> OrderStatus:
        return OrderStatus.FILLED
