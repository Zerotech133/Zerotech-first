"""盈亏日历服务 — V1「每日权益盈亏」口径 1:1 移植。

旧口径按 close_date 聚合已实现盈亏，无平仓的交易日为空 → 日历大片空白。
V1 口径（zhice/backend pnl_calendar_service）从持仓批次 × 日K收盘逐日重算：

  - 某日盈亏 = Σ 当日在仓批次的 数量 ×（当日价 − 基准价）
      基准价：开仓当日 = 开仓价（V2 简化，无逐笔成交成本表）；其后 = 当日 pre_close 昨收
              （已还原除权除息；缺 pre_close → 退回上一交易日 close → 再缺向前找最近可用；
               找不到跳过该批次当日，绝不兜底开仓价）
      当日价：平仓当日 = 平仓价 × (1 − 卖出费率)；否则 = 当日收盘
  - 交易日序列 = 持仓标的日K的 distinct 交易日
  - 盈亏比例基数 = 初始资金 + 截至前日累计盈亏（净值口径）
  - 策略过滤：strategy_version_id；不传 = 合并（初始资金 = 单账户 × 账户数）

V2 无 factor_series / live_price 表：当日若在日历内直接用收盘（CN 日线含当日结算）。
"""
from __future__ import annotations

import calendar as _calendar
from datetime import date, timedelta

from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session

# ─── 纯函数核心（可单测） ────────────────────────────────────────────────────


def compute_daily_pnl(
    lots: list[dict],
    closes: dict[tuple[str, date], float],
    trading_days: list[date],
    sell_cost: float = 0.0,
    pre_closes: dict[tuple[str, date], float] | None = None,
) -> tuple[dict[date, float], dict[date, int]]:
    """逐交易日计算全部批次的合计盈亏与在仓批次数。

    lots: [{symbol, qty, open_price, open_date, close_date|None, close_price|None}]
    closes: {(symbol, trade_date): close}
    pre_closes: {(symbol, trade_date): 当日昨收 pre_close}（已还原除权除息，与账户今日盈亏同口径）
    返回 ({trade_date: 当日盈亏}, {trade_date: 当日在仓批次数})。
    """
    from collections import defaultdict
    pre_closes = pre_closes or {}
    raw: dict[date, float] = defaultdict(float)
    counts: dict[date, int] = defaultdict(int)
    for lot in lots:
        sym = lot["symbol"]
        qty = lot["qty"]
        lot_daily: dict[date, float] = {}
        for i, d in enumerate(trading_days):
            if lot["open_date"] > d:
                continue
            if lot["close_date"] is not None and lot["close_date"] < d:
                break  # trading_days 升序：平仓后不再计该批次
            counts[d] += 1
            prev_day = trading_days[i - 1] if i > 0 else None

            # 当日价：平仓当日=卖出净价；否则=当日收盘（缺K跳过当日）
            if lot["close_date"] == d and lot["close_price"] is not None:
                price_d = lot["close_price"] * (1 - sell_cost)
            else:
                price_d = closes.get((sym, d))
                if price_d is None:
                    continue  # 停牌/缺数据：当日不计变动（次日以更早基准补齐）

            # 基准价：开仓当日=开仓价；其后=当日 pre_close（昨收，已还原除权除息，
            # 与 positions/account_stats 今日盈亏同口径）；pre_close 缺则退回上一交易日 close，
            # 再缺则向前找最近可用收盘。此前直接用上一日原始 close，会把除权除息跳空错算成盈亏。
            if lot["open_date"] == d:
                base = lot["open_price"]
            else:
                base = pre_closes.get((sym, d))
                if base is None and prev_day is not None:
                    base = closes.get((sym, prev_day))
                if base is None:
                    # 向前找最近一个可用收盘（停牌跨日）；仅在持仓期内找，找不到
                    # 就跳过当日，绝不兜底开仓价（否则会把多日涨幅错算成当日盈亏）
                    for pd_ in reversed(trading_days[:i]):
                        if pd_ < lot["open_date"]:
                            break
                        base = closes.get((sym, pd_))
                        if base is not None:
                            break
                    if base is None:
                        continue  # 无可用昨收基准 → 当日不计该批次变动

            lot_daily[d] = lot_daily.get(d, 0.0) + (price_d - base) * qty

        # 统一扣费：平仓 lot 锚定到真实 pnl_amount（把双边费/滑点结算在平仓日），
        # 使月度/累计合计精确 = 账户已实现净额（sum pnl_amount + 未实现 = 权益增值）。
        # 未提供 pnl_amount（未平仓 / 历史缺失）→ 保持毛盯市不动。
        cd = lot.get("close_date")
        pa = lot.get("pnl_amount")
        if cd is not None and pa is not None:
            gross = sum(lot_daily.values())
            lot_daily[cd] = lot_daily.get(cd, 0.0) + (float(pa) - gross)

        for d, v in lot_daily.items():
            raw[d] += v
    # 每个交易日都出条目（无在仓/无变动 → 0.0），与旧口径一致（前端按交易日铺格）
    result = {d: round(raw.get(d, 0.0), 2) for d in trading_days}
    counts_out = {d: counts.get(d, 0) for d in trading_days}
    return result, counts_out


def build_calendar_payload(
    year: int,
    month: int,
    daily_pnl: dict[date, float],
    initial_capital: float,
    daily_counts: dict[date, int] | None = None,
) -> dict:
    """按月切片 + 净值口径的盈亏比例 + 月度汇总。daily_pnl 须覆盖从起始日到月末。"""
    daily_counts = daily_counts or {}
    all_days = sorted(daily_pnl.keys())
    days_out = []
    cum_before = 0.0
    month_pnl = 0.0
    month_start_equity = None
    win = loss = 0
    for d in all_days:
        equity_prev = initial_capital + cum_before
        pnl = daily_pnl[d]
        if d.year == year and d.month == month:
            if month_start_equity is None:
                month_start_equity = equity_prev
            pct = round(pnl / equity_prev * 100, 4) if equity_prev > 0 else 0.0
            days_out.append({
                "date": d.isoformat(),
                "day": d.day,
                "pnl": pnl,
                "pnl_pct": pct,
                "equity": round(equity_prev + pnl, 2),
                "count": daily_counts.get(d, 0),
            })
            month_pnl += pnl
            if pnl > 0:
                win += 1
            elif pnl < 0:
                loss += 1
        cum_before += pnl

    month_pnl = round(month_pnl, 2)
    base = month_start_equity or initial_capital
    return {
        "year": year,
        "month": month,
        "initial_capital": initial_capital,
        "days": days_out,
        "month_pnl": month_pnl,
        "month_pnl_pct": round(month_pnl / base * 100, 4) if base > 0 else 0.0,
        "win_days": win,
        "loss_days": loss,
        "trading_days": len(days_out),
    }


# ─── 数据装配 ────────────────────────────────────────────────────────────────


def _load_lots(
    session,
    market: str,
    strategy_version_id: str | None,
    *,
    account_id: str | None = None,
    user_strategy_id: str | None = None,
) -> list[dict]:
    """全部批次（OPEN + CLOSED）：无 closed_at 的 CLOSED 脏数据按仍持有处理。

    owner-scoped 过滤（Phase B）：account_id / user_strategy_id 缺省 None=旧全局行为
    （不加归属过滤，legacy 逐字一致）；传入则只返回该账户/该实例归属的批次
    （NULL 归属的 legacy 行被排除，owner 视图不泄露全局仓）。
    """
    from zhice_os_v2.domain.market.registry import get_profile

    tz = get_profile(market).tz
    q = session.query(orm.PositionModel).filter(
        orm.PositionModel.market == market,
        orm.PositionModel.opened_at.isnot(None),
    )
    if strategy_version_id is not None:
        q = q.filter(orm.PositionModel.strategy_version_id == strategy_version_id)
    if account_id is not None:
        q = q.filter(orm.PositionModel.account_id == account_id)
    if user_strategy_id is not None:
        q = q.filter(orm.PositionModel.user_strategy_id == user_strategy_id)
    lots = []
    for row in q.all():
        qty = float(row.open_qty or 0)
        if qty <= 0:
            continue
        lots.append({
            "symbol": row.ts_code,
            "qty": qty,
            "open_price": float(row.open_price or 0),
            "open_date": row.opened_at.astimezone(tz).date(),
            "close_date": row.closed_at.astimezone(tz).date() if row.closed_at else None,
            "close_price": float(row.close_price) if row.close_price else None,
            # 统一扣费锚点：已平仓批次的真实已实现净额（含双边费/滑点），
            # 使日历合计与账户已实现分毫对齐；未平仓/缺失 → None（保持毛盯市）。
            "pnl_amount": (float(row.pnl_amount)
                           if row.closed_at and row.pnl_amount is not None else None),
        })
    return lots


def _initial_capital_for(session, market: str, strategy_version_id: str | None) -> float:
    """视图初始资金：子账户=本市场初始资金；合并=单账户 × 策略账户名册数。

    名册数取本市场 v2_strategy_version 的固定策略数（与 account 视图 len(accounts) 一致），
    而非「当前有持仓的 distinct 策略数」——后者会随「今天哪些策略恰好有仓」漂移，使
    pnl_pct/month_pnl_pct 的分母跨月不可比。
    """
    from zhice_os_v2.application.services.account_stats_service import _initial_capital

    if strategy_version_id is not None:
        return _initial_capital(market)
    n = (
        session.query(orm.StrategyVersionModel.strategy_version_id)
        .filter(orm.StrategyVersionModel.market == market)
        .distinct()
        .count()
    )
    return _initial_capital(market) * max(1, n)


def get_pnl_calendar(
    year: int | None,
    month: int | None,
    market: str = "CN",
    strategy_version_id: str | None = None,
) -> dict:
    """月度盈亏日历（V1 每日权益口径）。year/month 缺省取最近有数据的月份。"""
    from zhice_os_v2.domain.market.registry import get_profile

    session = get_session()
    try:
        lots = _load_lots(session, market, strategy_version_id)
        initial = _initial_capital_for(session, market, strategy_version_id)
        today = get_profile(market).trading_date()

        if lots:
            inception = min(l["open_date"] for l in lots)
        else:  # 无批次：日K全局最新交易日决定默认年月
            inception = (
                session.query(orm.BarDailyModel.trade_date)
                .filter(orm.BarDailyModel.market == market)
                .order_by(orm.BarDailyModel.trade_date.desc())
                .limit(1)
                .scalar()
            )

        # 年月缺省：最近一个有日K的 (year, month)（持仓标的存在数据）
        if year is None or month is None:
            if inception is None:
                return _empty_payload(market, initial)
            horizon = min(today, inception + timedelta(days=60))
            latest = (
                session.query(orm.BarDailyModel.trade_date)
                .filter(
                    orm.BarDailyModel.market == market,
                    orm.BarDailyModel.close.isnot(None),
                    orm.BarDailyModel.trade_date >= inception,
                    orm.BarDailyModel.trade_date <= horizon,
                )
                .order_by(orm.BarDailyModel.trade_date.desc())
                .limit(1)
                .scalar()
            )
            if latest is None:
                return _empty_payload(market, initial)
            year, month = latest.year, latest.month

        month_end = date(year, month, _calendar.monthrange(year, month)[1])
        horizon_end = min(month_end, today)
        if inception is None or horizon_end < inception:
            return _empty_payload(market, initial, year, month)

        symbols = sorted({l["symbol"] for l in lots})
        rows = (
            session.query(
                orm.BarDailyModel.symbol,
                orm.BarDailyModel.trade_date,
                orm.BarDailyModel.close,
                orm.BarDailyModel.pre_close,
            )
            .filter(
                orm.BarDailyModel.market == market,
                orm.BarDailyModel.symbol.in_(symbols),
                orm.BarDailyModel.close.isnot(None),
                orm.BarDailyModel.trade_date >= inception - timedelta(days=20),
                orm.BarDailyModel.trade_date <= horizon_end,
            )
            .all()
        )
        closes = {(r[0], r[1]): float(r[2]) for r in rows}
        # 当日 pre_close（昨收）：与账户今日盈亏同口径，避免除权除息跳空被错算
        pre_closes = {(r[0], r[1]): float(r[3]) for r in rows if r[3] is not None}
        trading_days = sorted({d for (_, d) in closes if inception <= d <= horizon_end})

        cost = get_profile(market).cost_model
        sell_cost = float(cost.commission_rate) + float(cost.stamp_tax_rate)
        daily, counts = compute_daily_pnl(
            lots, closes, trading_days, sell_cost=sell_cost, pre_closes=pre_closes,
        )
        payload = build_calendar_payload(year, month, daily, initial, daily_counts=counts)
        payload["market"] = market
        return payload
    finally:
        session.close()


def _empty_payload(market: str, initial: float, year: int | None = None,
                   month: int | None = None) -> dict:
    return {
        "market": market,
        "year": year,
        "month": month,
        "initial_capital": initial,
        "days": [],
        "month_pnl": 0.0,
        "month_pnl_pct": 0.0,
        "win_days": 0,
        "loss_days": 0,
        "trading_days": 0,
    }
