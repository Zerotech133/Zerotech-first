"""模拟券商撮合引擎：订单类型（市价/限价）、分档滑点、流动性部分成交、挂单/撤单。

撮合语义：
- 市价单：按流动性档位即时成交，剩余量撤单（不挂单）。
- 限价单：满足价格条件即时成交，否则挂单（PENDING），由 process_pending 盘中驱动。
- 分档规则（ratio = qty*price / avg_daily_turnover）：
  ratio <= 0.01  → 滑点 1x base，成交 100%
  0.01 < ratio <= 0.05 → 滑点 3x base，成交 90%
  0.05 < ratio <= 0.2  → 滑点 8x base，成交 60%
  ratio > 0.2   → 拒单 exceeds_liquidity
  无 turnover 数据 → base 滑点全量成交。
费用模型（保留 V1）：佣金（含最低佣金）、印花税（仅卖出）、过户费。
幂等：同一 order_id 重复下单返回首次结果。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from decimal import ROUND_DOWN, Decimal

from zhice_os_v2.domain.execution.broker import (
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
)
from zhice_os_v2.domain.market.context import MarketContext

_Q6 = Decimal("0.000001")
_ONE = Decimal("1")

# (slippage_multiple, fill_ratio) 按 ratio 升序；None 表示拒单
_TIERS: tuple[tuple[Decimal, float], ...] = (
    (Decimal("1"), 1.0),   # ratio <= 0.01
    (Decimal("3"), 0.9),   # 0.01 < ratio <= 0.05
    (Decimal("8"), 0.6),   # 0.05 < ratio <= 0.2
)


class SimulatedBroker:
    """按市场 Profile 施加分档滑点/手续费，按流动性部分成交，支持限价挂单。"""

    def __init__(self) -> None:
        self._results: dict[str, OrderResult] = {}
        self._statuses: dict[str, OrderStatus] = {}
        self._open_orders: dict[str, OrderRequest] = {}

    # ------------------------------------------------------------------
    # 下单
    # ------------------------------------------------------------------
    def place_order(self, ctx: MarketContext, request: OrderRequest) -> OrderResult:
        if request.order_id in self._results:
            return self._results[request.order_id]
        if request.order_type == OrderType.LIMIT:
            return self._place_limit(ctx, request)
        return self._fill_market(ctx, request)

    def _place_limit(self, ctx: MarketContext, request: OrderRequest) -> OrderResult:
        market_price = request.market_price
        if market_price is not None and self._limit_touchable(request, market_price):
            return self._fill_at(
                ctx,
                request,
                base_price=self._limit_fill_price(request, market_price),
            )
        return self._rest_order(request)

    def _rest_order(self, request: OrderRequest) -> OrderResult:
        self._open_orders[request.order_id] = request
        result = OrderResult(
            order_id=request.order_id,
            status=OrderStatus.PENDING,
            filled_qty=Decimal("0"),
            message="limit_order_resting",
            avg_fill_ratio=0.0,
            remaining_qty=request.qty,
        )
        self._statuses[request.order_id] = OrderStatus.PENDING
        self._results[request.order_id] = result
        return result

    # ------------------------------------------------------------------
    # 挂单驱动（盘中工作流每轮调用）
    # ------------------------------------------------------------------
    def process_pending(
        self, ctx: MarketContext, price_provider: Callable[[str], Decimal | None]
    ) -> list[OrderResult]:
        fills: list[OrderResult] = []
        for order_id, request in list(self._open_orders.items()):
            market_price = price_provider(request.ts_code)
            if market_price is None or not self._limit_touchable(request, market_price):
                continue
            result = self._fill_at(
                ctx,
                request,
                base_price=self._limit_fill_price(request, market_price),
            )
            fills.append(result)
        return fills

    # ------------------------------------------------------------------
    # 撤单 / 查询
    # ------------------------------------------------------------------
    def cancel_order(self, ctx: MarketContext, order_id: str) -> None:
        if order_id in self._open_orders:
            request = self._open_orders.pop(order_id)
            self._statuses[order_id] = OrderStatus.CANCELLED
            self._results[order_id] = OrderResult(
                order_id=order_id,
                status=OrderStatus.CANCELLED,
                filled_qty=Decimal("0"),
                message="cancelled_by_user",
                avg_fill_ratio=0.0,
                remaining_qty=request.qty,
            )

    def get_order_status(self, ctx: MarketContext, order_id: str) -> OrderStatus:
        if order_id in self._open_orders:
            return OrderStatus.PENDING
        return self._statuses.get(order_id, OrderStatus.REJECTED)

    # ------------------------------------------------------------------
    # 内部：价格条件与成交
    # ------------------------------------------------------------------
    @staticmethod
    def _limit_touchable(request: OrderRequest, market_price: Decimal) -> bool:
        if request.limit_price is None:
            return False
        if request.side == OrderSide.BUY:
            return market_price <= request.limit_price
        return market_price >= request.limit_price

    @staticmethod
    def _limit_fill_price(request: OrderRequest, market_price: Decimal) -> Decimal:
        """BUY 取 min(market, limit)，SELL 取 max(market, limit)。"""
        if request.limit_price is None:
            return market_price
        if request.side == OrderSide.BUY:
            return min(market_price, request.limit_price)
        return max(market_price, request.limit_price)

    def _fill_market(self, ctx: MarketContext, request: OrderRequest) -> OrderResult:
        base_price = request.limit_price or request.market_price or Decimal("0")
        if base_price <= 0:
            return self._terminal(self._reject(request, "missing_reference_price"))
        tier = self._liquidity_tier(request, base_price)
        if tier is None:
            return self._terminal(self._reject(request, "exceeds_liquidity"))
        filled_qty = self._integral_fill(request.qty, tier[1], self._allow_fractional(ctx))
        remaining = request.qty - filled_qty
        result = self._build_fill(ctx, request, base_price, tier, filled_qty, remaining)
        self._statuses[request.order_id] = result.status
        self._results[request.order_id] = result
        return result

    def _fill_at(self, ctx: MarketContext, request: OrderRequest, base_price: Decimal) -> OrderResult:
        """限价单成交：按档位成交，剩余量继续挂单。"""
        tier = self._liquidity_tier(request, base_price)
        if tier is None:
            return self._terminal(self._reject(request, "exceeds_liquidity"))
        filled_qty = self._integral_fill(request.qty, tier[1], self._allow_fractional(ctx))
        remaining = request.qty - filled_qty
        result = self._build_fill(ctx, request, base_price, tier, filled_qty, remaining)
        self._statuses[request.order_id] = result.status
        self._results[request.order_id] = result
        if remaining > 0:
            self._open_orders[request.order_id] = replace(request, qty=remaining)
        else:
            self._open_orders.pop(request.order_id, None)
        return result

    def _build_fill(
        self,
        ctx: MarketContext,
        request: OrderRequest,
        base_price: Decimal,
        tier: tuple[Decimal, float],
        filled_qty: Decimal,
        remaining: Decimal,
    ) -> OrderResult:
        slippage_multiple, fill_ratio = tier
        cost = ctx.profile.cost_model
        slip = cost.slippage_pct * slippage_multiple
        if request.side == OrderSide.BUY:
            filled_price = (base_price * (_ONE + slip)).quantize(_Q6)
        else:
            filled_price = (base_price * (_ONE - slip)).quantize(_Q6)
        turnover = filled_price * filled_qty
        commission = turnover * cost.commission_rate
        if cost.min_commission and commission < cost.min_commission:
            commission = cost.min_commission
        # 印花税仅卖出单边（CN 0.05% / HK 0.13% 双边由 profile 决定）
        stamp_tax = turnover * cost.stamp_tax_rate if request.side == OrderSide.SELL else Decimal("0")
        transfer_fee = turnover * cost.transfer_fee_rate
        fee = (commission + stamp_tax + transfer_fee).quantize(_Q6)
        fully_filled = filled_qty >= request.qty
        return OrderResult(
            order_id=request.order_id,
            status=OrderStatus.FILLED if fully_filled else OrderStatus.PARTIALLY_FILLED,
            filled_qty=filled_qty,
            filled_price=filled_price,
            fee=fee,
            message="simulated_filled_with_slippage_and_fee",
            avg_fill_ratio=float(fill_ratio) if filled_qty > 0 else 0.0,
            remaining_qty=Decimal("0") if fully_filled else remaining,
        )

    # ------------------------------------------------------------------
    # 内部：流动性分档
    # ------------------------------------------------------------------
    @staticmethod
    def _liquidity_tier(
        request: OrderRequest, price: Decimal
    ) -> tuple[Decimal, float] | None:
        """返回 (滑点倍数, 成交比例)；ratio>0.2 返回 None 表示拒单。"""
        if request.avg_daily_turnover is None or request.avg_daily_turnover <= 0:
            return _TIERS[0]  # 无数据 → base 滑点全量成交
        ratio = (request.qty * price) / request.avg_daily_turnover
        if ratio <= Decimal("0.01"):
            return _TIERS[0]
        if ratio <= Decimal("0.05"):
            return _TIERS[1]
        if ratio <= Decimal("0.2"):
            return _TIERS[2]
        return None

    @staticmethod
    def _allow_fractional(ctx: MarketContext) -> bool:
        """市场是否允许小数数量（CRYPTO 等）。profile 无该属性时按市场名兜底。"""
        allow = getattr(ctx.profile, "allow_fractional", None)
        if allow is None:
            allow = ctx.market == "CRYPTO"
        return bool(allow)

    @staticmethod
    def _integral_fill(qty: Decimal, fill_ratio: float, allow_fractional: bool = False) -> Decimal:
        """按成交比例计算成交量；整数股市场向下取整，小数市场保留 6 位。"""
        filled = qty * Decimal(str(fill_ratio))
        if allow_fractional:
            return filled.quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
        return filled.to_integral_value(rounding=ROUND_DOWN)

    def _terminal(self, result: OrderResult) -> OrderResult:
        """终态结果（拒单）也记录，保证幂等与状态查询一致。"""
        self._statuses[result.order_id] = result.status
        self._results[result.order_id] = result
        return result

    @staticmethod
    def _reject(request: OrderRequest, message: str) -> OrderResult:
        return OrderResult(
            order_id=request.order_id,
            status=OrderStatus.REJECTED,
            filled_qty=Decimal("0"),
            message=message,
            avg_fill_ratio=0.0,
        )
