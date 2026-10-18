"""跟单模拟盘领域：目标权重与再平衡下单的纯计算（无 IO）。float 入出，服务层用 Decimal 包装。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RebalanceOrder:
    symbol: str
    side: str      # BUY | SELL
    quantity: float
    price: float


def compute_target_mv(
    platform_mv: dict[str, float], tracked: set[str], total_platform_mv: float, equity: float
) -> dict[str, float]:
    """受跟标的目标市值 =(平台市值/平台总市值)*用户权益。分母=平台全部开仓市值；空/0 时返回 {}。"""
    if total_platform_mv <= 0:
        return {}
    return {
        sym: (platform_mv[sym] / total_platform_mv) * equity
        for sym in tracked
        if sym in platform_mv and platform_mv[sym] > 0
    }


def rebalance_orders(
    *, target_mv: dict[str, float], user_qty: dict[str, float], prices: dict[str, float],
    cash: float, equity: float, churn_threshold: float,
) -> list[RebalanceOrder]:
    """先卖后买向目标市值收敛；买入受现金约束不透支；|Δ市值|<=阈值*权益 不动。"""
    thresh = churn_threshold * equity
    orders: list[RebalanceOrder] = []
    avail = cash

    for sym, qty in user_qty.items():
        px = prices.get(sym)
        if not px or qty <= 0:
            continue
        cur_mv = qty * px
        tgt = target_mv.get(sym, 0.0)
        if cur_mv - tgt > thresh:
            sell_qty = min(qty, (cur_mv - tgt) / px)
            if sell_qty > 0:
                orders.append(RebalanceOrder(sym, "SELL", sell_qty, px))
                avail += sell_qty * px

    for sym, tgt in target_mv.items():
        px = prices.get(sym)
        if not px or tgt <= 0:
            continue
        cur_mv = user_qty.get(sym, 0.0) * px
        if tgt - cur_mv > thresh:
            buy_mv = min(tgt - cur_mv, avail)
            if buy_mv > 0:
                buy_qty = buy_mv / px
                orders.append(RebalanceOrder(sym, "BUY", buy_qty, px))
                avail -= buy_mv
    return orders
