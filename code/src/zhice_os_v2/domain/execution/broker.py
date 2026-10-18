"""执行层抽象：BrokerPort。

模拟盘、纸交易、实盘都通过同一接口接入。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from zhice_os_v2.domain.market.context import MarketContext


class ExecutionMode(StrEnum):
    SIMULATED = "SIMULATED"
    PAPER = "PAPER"
    LIVE = "LIVE"


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


@dataclass(frozen=True)
class OrderRequest:
    order_id: str
    market: str
    ts_code: str
    side: OrderSide
    qty: Decimal
    limit_price: Decimal | None = None
    execution_mode: ExecutionMode = ExecutionMode.SIMULATED
    strategy_version_id: int | None = None
    approval_request_id: str | None = None
    order_type: OrderType = OrderType.MARKET
    # 日均成交额（用于流动性分档：滑点倍数/部分成交比例/拒单）
    avg_daily_turnover: Decimal | None = None
    # 下单时最新市价（限价单判断能否立即成交）
    market_price: Decimal | None = None


@dataclass(frozen=True)
class OrderResult:
    order_id: str
    status: OrderStatus
    filled_qty: Decimal = Decimal("0")
    filled_price: Decimal | None = None
    fee: Decimal = Decimal("0")
    message: str | None = None
    # 本次成交比例（filled_qty / qty），1.0 表示全量成交
    avg_fill_ratio: float = 1.0
    # 未成交剩余量（部分成交或挂单未成交部分）；全量成交/拒单为 None
    remaining_qty: Decimal | None = None


class BrokerPort(Protocol):
    def place_order(self, ctx: MarketContext, request: OrderRequest) -> OrderResult: ...
    def cancel_order(self, ctx: MarketContext, order_id: str) -> None: ...
    def get_order_status(self, ctx: MarketContext, order_id: str) -> OrderStatus: ...
