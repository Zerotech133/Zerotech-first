"""仓位计算 — 组合预算制 + 组合级天花板 + 三重约束取最小，按 A股整手（100股）取整。

约束：
  C0 最大持仓数：已持有 max_positions 只、且本次为新标的 → 返回 0（不再开新仓，加仓不受限）
  C1 组合预算制仓位上限（替代死板的单股固定5%）：
       每个持仓"名额"的预算 = slot（取计划 max_position_ratio，默认5%）
       新仓可用预算 = (在仓只数 + 1) × slot − 当前总仓位占比
       即：现有持仓没用满的预算可以让给新仓
       （例：5只在仓共占24%，slot=5% → 第6只预算 = 30% − 24% = 6%）
       单笔封顶 = slot × SINGLE_POSITION_CEILING_MULT（防闲置预算导致过度集中）
  C1t 组合总仓位天花板：新仓不得把总仓位推过 max_total_exposure_pct（留现金缓冲，不满仓瞎赌）
  C2 风险预算：若触发止损，亏损 ≤ 账户净值 × risk_budget（默认2%）
  C3 可用资金：花费（含手续费）≤ 可用现金

qty = min(C1, C1t, C2, C3) 向下取整到 100 的倍数；不足一手返回 0（放弃执行）。
"""
from __future__ import annotations

import math
from decimal import Decimal

from zhice_os_v2.domain.market.registry import get_profile

# A股整手/佣金真源已迁至 CNProfile;此处模块级取值维持既有引用点
LOT_SIZE = get_profile("CN").lot_size()                                # 100
COMMISSION_RATE = float(get_profile("CN").cost_model.commission_rate)  # 0.0003

DEFAULT_MAX_POSITION_RATIO = 0.05      # 每个持仓名额的预算（slot）
DEFAULT_RISK_BUDGET_PCT = 0.02
SINGLE_POSITION_CEILING_MULT = 2.0     # 单笔仓位封顶 = slot × 2（默认10%）


def compute_position_size(
    price: float,
    net_value: float,
    max_position_ratio: float | None = None,
    risk_budget_pct: float | None = None,
    stop_loss_price: float | None = None,
    available_cash: float | None = None,
    open_position_count: int = 0,
    current_exposure_value: float = 0.0,
    current_symbol_exposure_value: float = 0.0,
    max_total_exposure_pct: float | None = None,
    max_positions: int | None = None,
    distinct_position_count: int = 0,
    symbol_ceiling_pct: float | None = None,
    market: str = "CN",
    lot_size: int | None = None,
) -> int | Decimal:
    """返回应买入的股数（100的整数倍），不足一手返回 0。

    open_position_count / current_exposure_value：当前在仓只数与总持仓成本（组合预算制）。
    current_symbol_exposure_value：同标的已持仓成本——二次加仓时，
    该标的合计仓位不得超过 slot × SINGLE_POSITION_CEILING_MULT（默认10%），
    预算用满自动返回 0（过滤二次信号）。
    max_total_exposure_pct：组合总仓位上限（如 0.80，留 20% 现金）；None 不限。
    max_positions / distinct_position_count：单账户最多持仓只数与当前不同标的数；
    达上限且本次为新标的时返回 0（加仓到已有标的不受此闸限制）。
    """
    profile = get_profile(market)
    # lot_size 优先用调用方按标的解析的每手股数（港股每手异构，见 position_sizing_service），
    # 未提供则回退市场画像默认（A股100 / 美股1 / 港股画像默认）。
    lot = lot_size if (lot_size and lot_size > 0) else profile.lot_size()
    commission = float(profile.cost_model.commission_rate)

    if not price or price <= 0 or not net_value or net_value <= 0:
        return 0

    # crypto 调用点可传 Decimal 价/净值；内部为 float 数学，统一归一。
    # 对股票逐字节无影响：float(既有 float) 值不变。
    price = float(price)
    net_value = float(net_value)

    is_new_symbol = current_symbol_exposure_value <= 0

    # C0 最大持仓数：已达上限、且开的是新标的 → 不再开新仓
    if max_positions is not None and is_new_symbol and distinct_position_count >= max_positions:
        return 0

    slot = max_position_ratio if max_position_ratio and max_position_ratio > 0 \
        else DEFAULT_MAX_POSITION_RATIO
    risk_pct = risk_budget_pct if risk_budget_pct and risk_budget_pct > 0 \
        else DEFAULT_RISK_BUDGET_PCT
    ceiling_pct = slot * SINGLE_POSITION_CEILING_MULT
    symbol_ceiling = symbol_ceiling_pct if (symbol_ceiling_pct and symbol_ceiling_pct > 0) else ceiling_pct

    # C1 组合预算制：新仓预算 = (N+1)×slot − 当前总仓位占比，封顶 slot×2
    exposure_pct = max(0.0, current_exposure_value / net_value)
    budget_pct = (open_position_count + 1) * slot - exposure_pct
    budget_pct = min(max(0.0, budget_pct), ceiling_pct)

    # C1t 组合总仓位天花板：新仓不得把总仓位推过 max_total_exposure_pct（留现金缓冲）
    if max_total_exposure_pct is not None:
        budget_pct = min(budget_pct, max(0.0, max_total_exposure_pct - exposure_pct))

    # C1b 单标的预算：同标的合计（已持 + 本次）≤ 封顶（二次加仓的裁决线）
    symbol_pct = max(0.0, current_symbol_exposure_value / net_value)
    symbol_budget_pct = max(0.0, symbol_ceiling - symbol_pct)
    budget_pct = min(budget_pct, symbol_budget_pct)

    qty_by_cap = (net_value * budget_pct) / price

    # C2 风险预算（有有效止损价才启用）
    qty_by_risk = float("inf")
    if stop_loss_price and 0 < stop_loss_price < price:
        per_share_risk = price - stop_loss_price
        qty_by_risk = (net_value * risk_pct) / per_share_risk

    # C3 可用资金（含手续费）
    qty_by_cash = float("inf")
    if available_cash is not None:
        qty_by_cash = available_cash / (price * (1 + commission))

    qty = min(qty_by_cap, qty_by_risk, qty_by_cash)
    if getattr(profile, "allow_fractional", False):
        # crypto：小数量(6 位精度)，不向下取整到整手；负预算/负现金 → 0
        return Decimal(str(round(max(0.0, qty), 6)))
    lots = max(0, math.floor(qty / lot))  # 负预算/负现金 → 0，绝不返回负股数
    return int(lots * lot)
