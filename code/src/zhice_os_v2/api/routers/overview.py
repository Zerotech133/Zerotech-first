"""总览简报 API（V1 system/overview 对齐）。"""
from __future__ import annotations

import logging
from datetime import datetime
from enum import StrEnum

from fastapi import APIRouter, Depends

logger = logging.getLogger(__name__)
from sqlalchemy import func

from zhice_os_v2.api.dependencies import get_current_user, get_db
from zhice_os_v2.application.services.account_stats_service import AccountStatsService
from zhice_os_v2.application.services.alert_service import AlertService
from zhice_os_v2.domain.alert.alert import AlertStatus
from zhice_os_v2.domain.market.registry import get_profile
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

router = APIRouter(prefix="/overview", tags=["overview"], dependencies=[Depends(get_current_user)])


class Phase(StrEnum):
    PRE_MARKET = "PRE_MARKET"      # 盘前
    TRADING = "TRADING"            # 盘中
    LUNCH_BREAK = "LUNCH_BREAK"    # 午间休市
    POST_MARKET = "POST_MARKET"    # 盘后
    CLOSED = "CLOSED"              # 休市（非交易日）


def _is_trading_day(market: str, today) -> bool:
    """当日是否交易日：v2_market_calendar 有记录用 is_open，否则 profile 回退口径。"""
    session = get_session()
    try:
        row = (
            session.query(orm.MarketCalendarModel.is_open)
            .filter(
                orm.MarketCalendarModel.market == market,
                orm.MarketCalendarModel.trade_date == today,
            )
            .first()
        )
        if row is not None:
            return bool(row[0])
    except Exception:
        logger.warning("_is_trading_day 查询失败 market=%s，回落 profile 判断", market, exc_info=True)
    finally:
        session.close()
    return get_profile(market).fallback_is_open(today)


def _market_phase(market: str) -> Phase:
    """按市场时区当前时刻 + sessions() 判阶段（V1 _market_phase 口径）。"""
    profile = get_profile(market)
    now = datetime.now(tz=profile.tz)
    if not _is_trading_day(market, now.date()):
        return Phase.CLOSED
    sessions = profile.sessions()
    if not sessions:
        return Phase.CLOSED
    t = now.time()
    if t < sessions[0].open:
        return Phase.PRE_MARKET
    if t >= sessions[-1].close:
        return Phase.POST_MARKET
    for s in sessions:
        if s.open <= t < s.close:
            return Phase.TRADING
    return Phase.LUNCH_BREAK  # 段间隙（双段市场如 CN/HK 午休）


def _latest_trade_date(market: str, today):
    """该市场最新交易日：v2_market_calendar 最近开市日，无日历回退 bar_daily max。"""
    session = get_session()
    try:
        d = (
            session.query(func.max(orm.MarketCalendarModel.trade_date))
            .filter(
                orm.MarketCalendarModel.market == market,
                orm.MarketCalendarModel.is_open.is_(True),
                orm.MarketCalendarModel.trade_date <= today,
            )
            .scalar()
        )
        if d is None:
            d = (
                session.query(func.max(orm.BarDailyModel.trade_date))
                .filter(orm.BarDailyModel.market == market)
                .scalar()
            )
        return d
    except Exception:
        logger.exception("_latest_trade_date 查询失败 market=%s", market)
        return None
    finally:
        session.close()


def _candidate_count(market: str) -> int:
    """最新池日候选标的数（candidate_asset join pool 最新 pool_date）。"""
    session = get_session()
    try:
        latest = (
            session.query(func.max(orm.CandidatePoolModel.pool_date))
            .filter(orm.CandidatePoolModel.market == market)
            .scalar()
        )
        count = 0
        if latest is not None:
            count = (
                session.query(func.count(orm.CandidateAssetModel.id))
                .join(
                    orm.CandidatePoolModel,
                    orm.CandidatePoolModel.pool_id == orm.CandidateAssetModel.pool_id,
                )
                .filter(
                    orm.CandidatePoolModel.market == market,
                    orm.CandidatePoolModel.pool_date == latest,
                    orm.CandidateAssetModel.market == market,
                )
                .scalar()
                or 0
            )
        return int(count)
    except Exception:
        logger.exception("_candidate_count 查询失败 market=%s", market)
        return 0
    finally:
        session.close()


@router.get("")
def overview(market: str = "CN", db: PersistencePort = Depends(get_db)):
    """六指标 + 市场阶段（V1 system/overview 对齐）。"""
    profile = get_profile(market)
    now = datetime.now(tz=profile.tz)
    today = now.date()

    trade_date = _latest_trade_date(market, today)
    phase = _market_phase(market)
    candidates = _candidate_count(market)
    # 仅统计当前市场的待授权（此前不按 market 过滤，CN 工作台显示了全部 4 市场合计，
    # 与流水线漏斗的 CN 口径打架）
    approvals_pending = sum(
        1 for r in db.list_approvals() if r.status == "PENDING" and r.market == market
    )
    # 活跃告警：与 alerts 路由同一数据源（经 PersistencePort，随后端切换），OPEN 计数
    active_alerts = len(AlertService(db).list_alerts(status=AlertStatus.OPEN))

    summary = AccountStatsService(db).market_summary(market)

    return {
        "market": market,
        "trade_date": trade_date.isoformat() if trade_date else None,
        "phase": phase.value,
        "candidates": candidates,
        "approvals_pending": approvals_pending,
        "active_alerts": active_alerts,
        "today_pnl": summary.get("today_pnl", 0),
        "total_equity": summary.get("total_equity", 0),
    }
