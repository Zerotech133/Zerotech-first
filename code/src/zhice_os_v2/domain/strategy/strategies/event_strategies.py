"""事件信号三策略（V1 移植：earnings_surprise / holder_buy / top_net_buy）。

V1 参数 1:1：
  EARN_SURPRISE 预增/略增、p_change_min ∈ [50%, 300%]、公告 3 日窗（PEAD 漂移）
  HOLDER_BUY    in_de=IN、change_ratio ≥ 0.5%、公告 3 日窗
  TOP_NET_BUY   净买入 > 0、上榜 3 日窗（按 trade_date）
均按幅度降序取 topN，score = 幅度。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)

_WINDOW_DAYS = 3


def _window_lo(session, orm, market: str, as_of: date) -> date:
    """事件窗下界（交易日口径）：最近 _WINDOW_DAYS 个开市交易日中最早者（含 as_of 当日若开市）。

    修 P2-J：原 `as_of - timedelta(days=_WINDOW_DAYS)` 用自然日，周末/长假后系统性漏采
    （周一扫描下界=周五、`> 周五` 把周五事件排除）。改查 v2_market_calendar 交易日历，
    调用方过滤统一用「>= 下界」（含边界）。日历缺失→按 profile.fallback_is_open 回退。
    """
    rows = (
        session.query(orm.MarketCalendarModel.trade_date)
        .filter(
            orm.MarketCalendarModel.market == market,
            orm.MarketCalendarModel.is_open.is_(True),
            orm.MarketCalendarModel.trade_date <= as_of,
        )
        .order_by(orm.MarketCalendarModel.trade_date.desc())
        .limit(_WINDOW_DAYS)
        .all()
    )
    if rows:
        return min(r[0] for r in rows)
    return _fallback_window_lo(market, as_of)


def _fallback_window_lo(market: str, as_of: date) -> date:
    """日历缺失兜底：按 profile.fallback_is_open 向前数 _WINDOW_DAYS 个交易日取最早者。

    profile 亦不可用（未知市场）→ 降级放宽 days=5 自然日，宁可多采不漏采。
    """
    try:
        from zhice_os_v2.domain.market.registry import get_profile

        is_open = get_profile(market).fallback_is_open
    except Exception:  # noqa: BLE001 - 未知市场/画像不可用，走自然日兜底
        return as_of - timedelta(days=5)

    lo = as_of
    found = 0
    d = as_of
    guard = 0
    while found < _WINDOW_DAYS and guard < 60:
        if is_open(d):
            found += 1
            lo = d
            if found >= _WINDOW_DAYS:
                break
        d -= timedelta(days=1)
        guard += 1
    return lo


def _select_earn_surprise(trade_date: date, max_candidates: int = 20,
                          market: str = "CN") -> list[dict]:
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session

    session = get_session()
    try:
        from sqlalchemy import func
        lo, hi = _window_lo(session, orm, market, trade_date), trade_date
        # Portable replacement for PostgreSQL-only DISTINCT ON (ts_code):
        # subquery gets max p_change_min per ts_code, join retrieves full row.
        _base_filter = [
            orm.EarningsForecastModel.ann_date >= lo,
            orm.EarningsForecastModel.ann_date <= hi,
            orm.EarningsForecastModel.forecast_type.in_(("预增", "略增")),
            orm.EarningsForecastModel.p_change_min >= 50.0,
            orm.EarningsForecastModel.p_change_min <= 300.0,
        ]
        subq = (
            session.query(
                orm.EarningsForecastModel.ts_code,
                func.max(orm.EarningsForecastModel.p_change_min).label("max_pct"),
            )
            .filter(*_base_filter)
            .group_by(orm.EarningsForecastModel.ts_code)
            .subquery()
        )
        rows = (
            session.query(
                orm.EarningsForecastModel.ts_code,
                orm.EarningsForecastModel.p_change_min,
                orm.EarningsForecastModel.forecast_type,
            )
            .join(
                subq,
                (orm.EarningsForecastModel.ts_code == subq.c.ts_code)
                & (orm.EarningsForecastModel.p_change_min == subq.c.max_pct),
            )
            .filter(*_base_filter)
            .all()
        )
    finally:
        session.close()
    rows = sorted(rows, key=lambda r: -float(r[1] or 0))[:max_candidates]
    return [{"security_id": "", "ts_code": r[0], "score": float(r[1] or 0),
             "reason": f"业绩预告{r[2]}：净利预增{float(r[1]):.0f}%（PEAD 漂移）"}
            for r in rows]


def _select_holder_buy(trade_date: date, max_candidates: int = 20,
                       market: str = "CN") -> list[dict]:
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session

    session = get_session()
    try:
        from sqlalchemy import func
        lo, hi = _window_lo(session, orm, market, trade_date), trade_date
        # Portable replacement for PostgreSQL-only DISTINCT ON (ts_code):
        # subquery gets max change_ratio per ts_code, join retrieves full row.
        _base_filter = [
            orm.HolderTradeModel.ann_date >= lo,
            orm.HolderTradeModel.ann_date <= hi,
            orm.HolderTradeModel.in_de == "IN",
            orm.HolderTradeModel.change_ratio >= 0.5,
        ]
        subq = (
            session.query(
                orm.HolderTradeModel.ts_code,
                func.max(orm.HolderTradeModel.change_ratio).label("max_ratio"),
            )
            .filter(*_base_filter)
            .group_by(orm.HolderTradeModel.ts_code)
            .subquery()
        )
        rows = (
            session.query(
                orm.HolderTradeModel.ts_code,
                orm.HolderTradeModel.change_ratio,
            )
            .join(
                subq,
                (orm.HolderTradeModel.ts_code == subq.c.ts_code)
                & (orm.HolderTradeModel.change_ratio == subq.c.max_ratio),
            )
            .filter(*_base_filter)
            .all()
        )
    finally:
        session.close()
    rows = sorted(rows, key=lambda r: -float(r[1] or 0))[:max_candidates]
    return [{"security_id": "", "ts_code": r[0], "score": float(r[1] or 0),
             "reason": f"大股东/董监高增持 {float(r[1]):.2f}%（占流通比）"}
            for r in rows]


def _select_top_net_buy(trade_date: date, max_candidates: int = 20,
                        market: str = "CN") -> list[dict]:
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session

    session = get_session()
    try:
        from sqlalchemy import func
        lo, hi = _window_lo(session, orm, market, trade_date), trade_date
        # Portable replacement for PostgreSQL-only DISTINCT ON (ts_code):
        # subquery gets the max net_amount per ts_code in the window, then
        # join retrieves the matching full row (ts_code, net_amount, trade_date).
        subq = (
            session.query(
                orm.TopListModel.ts_code,
                func.max(orm.TopListModel.net_amount).label("max_net"),
            )
            .filter(orm.TopListModel.trade_date >= lo,
                    orm.TopListModel.trade_date <= hi,
                    orm.TopListModel.net_amount > 0)
            .group_by(orm.TopListModel.ts_code)
            .subquery()
        )
        rows = (
            session.query(
                orm.TopListModel.ts_code,
                orm.TopListModel.net_amount,
                orm.TopListModel.trade_date,
            )
            .join(
                subq,
                (orm.TopListModel.ts_code == subq.c.ts_code)
                & (orm.TopListModel.net_amount == subq.c.max_net),
            )
            .filter(orm.TopListModel.trade_date >= lo,
                    orm.TopListModel.trade_date <= hi)
            .all()
        )
    finally:
        session.close()
    rows = sorted(rows, key=lambda r: -float(r[1] or 0))[:max_candidates]
    return [{"security_id": "", "ts_code": r[0], "score": float(r[1] or 0),
             "reason": f"龙虎榜净买入 {float(r[1]) / 1e8:.2f} 亿（{r[2]:%m-%d}）"}
            for r in rows]


# V1 签名对齐（trade_date, max_candidates, market）
select_earnings_surprise = _select_earn_surprise
select_holder_buy = _select_holder_buy
select_top_net_buy = _select_top_net_buy
