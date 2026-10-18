"""仓位计算服务 — V1 五重约束的 V2 DB 适配层。

`domain/execution/position_sizing.compute_position_size`（纯函数，V1 语义 1:1）
负责约束数学；本服务负责从 DB 聚合当前组合状态并调它：

  净值基数 = 各市场初始资金 + 该策略(sv)已实现盈亏（子账户口径，V1 同）
  在仓只数/总仓位/同标的仓位 = 该 sv 的 OPEN 持仓成本聚合
  可用现金 = 净值 − Σ(持仓成本×(1+佣金))  —— V1 SimulatedBroker._portfolio_state 语义

V1 预算制哲学：slot=5%（计划的 max_position_ratio），未用满的预算让给新仓，
单笔封顶 slot×2=10%；总仓位天花板 80%（CRYPTO 50%）；风险预算 2%/止损距离；
A股整手取整、CRYPTO 6 位小数。
"""
from __future__ import annotations

import logging
from decimal import Decimal

from zhice_os_v2.domain.execution.position_sizing import compute_position_size
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

logger = logging.getLogger(__name__)

# V1 settings 默认（sim_max_positions / sim_max_total_exposure_pct）
DEFAULT_MAX_POSITIONS = 10
DEFAULT_MAX_TOTAL_EXPOSURE_PCT = 0.80


def _strategy_positions(db: PersistencePort, sv: str) -> list:
    """该策略版本的 OPEN 持仓（domain PositionLot）。"""
    return [p for p in db.list_open_positions() if p.strategy_version_id == sv]


def _realized_pnl(db: PersistencePort, sv: str) -> float:
    """该 sv CLOSED 持仓已实现盈亏合计（realized 口径）。"""
    try:
        from sqlalchemy import func, select

        from zhice_os_v2.infrastructure.db import models as orm
        from zhice_os_v2.infrastructure.db.session import get_session

        session = get_session()
        try:
            v = session.execute(
                select(func.coalesce(func.sum(orm.PositionModel.pnl_amount), 0))
                .where(orm.PositionModel.strategy_version_id == sv,
                       orm.PositionModel.status == "CLOSED")
            ).scalar()
            return float(v or 0)
        finally:
            session.close()
    except Exception:
        return 0.0


def _open_stats(db: PersistencePort, sv: str) -> dict:
    """该 sv 的 OPEN 持仓聚合：只数/总成本/同标的成本映射。"""
    positions = _strategy_positions(db, sv)
    by_symbol: dict[str, Decimal] = {}
    total_cost = Decimal("0")
    for p in positions:
        cost = (p.open_price or Decimal("0")) * (p.open_qty or Decimal("0"))
        total_cost += cost
        by_symbol[p.ts_code] = by_symbol.get(p.ts_code, Decimal("0")) + cost
    return {
        "count": len(positions),
        "distinct": len(by_symbol),
        "total_cost": total_cost,
        "by_symbol": by_symbol,
    }


def _lot_size(market: str, ts_code: str) -> int | None:
    """该标的每手股数：优先查 v2_instrument.lot_size（港股每手异构，HKEX 官方值经
    ingest 入库），查不到/异常回退 None（compute_position_size 再退市场画像默认）。"""
    try:
        from sqlalchemy import select

        from zhice_os_v2.infrastructure.db import models as orm
        from zhice_os_v2.infrastructure.db.session import get_session

        session = get_session()
        try:
            v = session.execute(
                select(orm.InstrumentModel.lot_size).where(
                    orm.InstrumentModel.market == market,
                    orm.InstrumentModel.symbol == ts_code,
                )
            ).scalar()
            return int(v) if v else None
        finally:
            session.close()
    except Exception:
        return None


def size_new_position(
    db: PersistencePort,
    market: str,
    sv: str,
    ts_code: str,
    price: float | Decimal,
    stop_loss_price: float | None = None,
    max_position_ratio: float | None = None,
    max_total_exposure_pct: float | None = None,
    max_positions: int | None = None,
) -> int | Decimal:
    """新仓数量（V1 五重约束）。返回 0 = 预算满/超上限，调用方应跳过建仓。"""
    from zhice_os_v2.application.services.account_stats_service import _initial_capital
    from zhice_os_v2.domain.market.registry import get_profile

    profile = get_profile(market)
    stats = _open_stats(db, sv)
    net_value = _initial_capital(market) + _realized_pnl(db, sv)
    if net_value <= 0:
        return 0

    exposure_value = float(stats["total_cost"])
    symbol_exposure = float(stats["by_symbol"].get(ts_code, Decimal("0")))
    # 可用现金 = 净值 − 已持成本×(1+佣金)（V1 语义：扣手续费防买穿负数）
    commission = float(profile.cost_model.commission_rate)
    available_cash = net_value - exposure_value * (1 + commission)

    # CRYPTO profile 覆盖（V1: max_position_ratio 0.10 / 总敞口 0.50）
    if max_position_ratio is None:
        max_position_ratio = 0.10 if market == "CRYPTO" else 0.05
    if max_total_exposure_pct is None:
        pct = getattr(
            profile, "max_total_exposure_pct", lambda: DEFAULT_MAX_TOTAL_EXPOSURE_PCT)()
        max_total_exposure_pct = float(pct)
    if max_positions is None:
        max_positions = DEFAULT_MAX_POSITIONS

    qty = compute_position_size(
        price=float(price),
        net_value=net_value,
        max_position_ratio=max_position_ratio,
        risk_budget_pct=None,          # 默认 2%（compute 内部 DEFAULT）
        stop_loss_price=stop_loss_price,
        available_cash=available_cash,
        open_position_count=stats["count"],
        current_exposure_value=exposure_value,
        current_symbol_exposure_value=symbol_exposure,
        max_total_exposure_pct=max_total_exposure_pct,
        max_positions=max_positions,
        distinct_position_count=stats["distinct"],
        market=market,
        lot_size=_lot_size(market, ts_code),
    )
    qty_num = float(qty)
    if qty_num <= 0:
        logger.info(
            "position_size=0 (预算满/超限): market=%s sv=%s ts=%s price=%s net=%s "
            "open=%s distinct=%s exposure=%.0f",
            market, sv, ts_code, price, round(net_value), stats["count"],
            stats["distinct"], exposure_value,
        )
    return qty
