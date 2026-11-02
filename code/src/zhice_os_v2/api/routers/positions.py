"""持仓 API。"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException

logger = logging.getLogger(__name__)
from pydantic import BaseModel
from sqlalchemy import func

from zhice_os_v2.api.dependencies import deny_viewer, get_current_user, get_db, require_roles
from zhice_os_v2.application.services.account_stats_service import AccountStatsService
from zhice_os_v2.application.services.learning_service import LearningService
from zhice_os_v2.application.services.replay_service import ReplayService
from zhice_os_v2.domain.market.registry import get_profile
from zhice_os_v2.domain.portfolio.position import PositionStatus
from zhice_os_v2.domain.security.user import Role, User
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

router = APIRouter(prefix="/positions", tags=["positions"], dependencies=[Depends(get_current_user)])


def _get_daily_close_prices(symbols: list[str], market: str) -> dict[str, float]:
    """批量查各标的最新日线收盘价（base 价）。"""
    if not symbols:
        return {}
    session = get_session()
    try:
        subq = (
            session.query(
                orm.BarDailyModel.symbol,
                func.max(orm.BarDailyModel.trade_date).label("max_date"),
            )
            .filter(
                orm.BarDailyModel.symbol.in_(symbols),
                orm.BarDailyModel.market == market,
            )
            .group_by(orm.BarDailyModel.symbol)
            .subquery()
        )
        rows = (
            session.query(orm.BarDailyModel.symbol, orm.BarDailyModel.close)
            .join(
                subq,
                (orm.BarDailyModel.symbol == subq.c.symbol)
                & (orm.BarDailyModel.trade_date == subq.c.max_date),
            )
            .all()
        )
        return {r[0]: float(r[1]) for r in rows}
    except Exception:
        logger.exception("_get_daily_close_prices 查询失败 market=%s", market)
        return {}
    finally:
        session.close()


def _get_snapshot_prices(symbols: list[str], market: str) -> dict[str, float]:
    """读 v2_latest_snapshot 中【新鲜】的盘中实时价（snapshot_time 不早于最新交易日）。

    表不存在/无新鲜数据时返回 {}（调用方回落日线，零破坏）。
    """
    if not symbols:
        return {}
    session = get_session()
    try:
        q = session.query(
            orm.LatestSnapshotModel.symbol, orm.LatestSnapshotModel.price
        ).filter(
            orm.LatestSnapshotModel.symbol.in_(symbols),
            orm.LatestSnapshotModel.market == market,
            orm.LatestSnapshotModel.price.isnot(None),
        )
        latest_td = _get_latest_trade_date(market)
        if latest_td is not None:
            # 只用不早于最新交易日的快照（陈旧快照忽略，回落日线）
            q = q.filter(orm.LatestSnapshotModel.snapshot_time >= latest_td)
        rows = q.all()
        return {r[0]: float(r[1]) for r in rows if r[1] is not None}
    except Exception:
        logger.warning("_get_snapshot_prices 快照查询失败，回落日线价 market=%s", market, exc_info=True)
        return {}
    finally:
        session.close()


def _get_latest_prices(symbols: list[str], market: str) -> dict[str, float]:
    """批量查各标的最新价：日线收盘为 base，有【新鲜实时快照】则覆盖。

    盘中风控/持仓估值经此取价；快照缺失或陈旧时与原日线口径完全一致（回落兜底）。
    """
    if not symbols:
        return {}
    prices = _get_daily_close_prices(symbols, market)
    prices.update(_get_snapshot_prices(symbols, market))
    return prices


def _get_latest_trade_date(market: str) -> date | None:
    """市场最新日线交易日（V1 latest_trading_day 口径：今日盈亏的参照日）。"""
    session = get_session()
    try:
        d = (
            session.query(func.max(orm.BarDailyModel.trade_date))
            .filter(orm.BarDailyModel.market == market)
            .scalar()
        )
        return d
    except Exception:
        logger.exception("_get_latest_trade_date 查询失败 market=%s", market)
        return None
    finally:
        session.close()


def _current_trading_date(market: str) -> date | None:
    """T+1 可用性判断基准：市场时区「当前交易日」（今天若开市，否则最近开市日）。

    区别于 _get_latest_trade_date（最新日K trade_date）——盘中今日日K尚未入库时后者滞后一日，
    若用它判「当日开仓」会把昨仓误判为今日开仓 → available_qty=0（T+1 收盘前系统性误清零）。
    交易日历缺失则回退系统当前日期。
    """
    tz = get_profile(market).tz
    today = datetime.now(tz).date()
    session = get_session()
    try:
        row = (
            session.query(orm.MarketCalendarModel.trade_date)
            .filter(orm.MarketCalendarModel.market == market,
                    orm.MarketCalendarModel.trade_date <= today,
                    orm.MarketCalendarModel.is_open.is_(True))
            .order_by(orm.MarketCalendarModel.trade_date.desc())
            .first()
        )
        return row[0] if row else today
    except Exception:
        logger.exception("_current_trading_date 查询失败 market=%s", market)
        return today
    finally:
        session.close()


def _get_pre_closes_by_bars(symbols: list[str], market: str) -> dict[str, float]:
    """昨收回退口径：最近第 2 根 close（无 pre_close 列数据时用）。"""
    if not symbols:
        return {}
    session = get_session()
    try:
        subq = (
            session.query(
                orm.BarDailyModel.symbol,
                func.max(orm.BarDailyModel.trade_date).label("max_date"),
            )
            .filter(
                orm.BarDailyModel.symbol.in_(symbols),
                orm.BarDailyModel.market == market,
            )
            .group_by(orm.BarDailyModel.symbol)
            .subquery()
        )
        second = (
            session.query(
                orm.BarDailyModel.symbol,
                func.max(orm.BarDailyModel.trade_date).label("second_date"),
            )
            .join(
                subq,
                (orm.BarDailyModel.symbol == subq.c.symbol)
                & (orm.BarDailyModel.trade_date < subq.c.max_date),
            )
            .filter(
                orm.BarDailyModel.symbol.in_(symbols),
                orm.BarDailyModel.market == market,
            )
            .group_by(orm.BarDailyModel.symbol)
            .subquery()
        )
        rows = (
            session.query(orm.BarDailyModel.symbol, orm.BarDailyModel.close)
            .join(
                second,
                (orm.BarDailyModel.symbol == second.c.symbol)
                & (orm.BarDailyModel.trade_date == second.c.second_date),
            )
            .all()
        )
        return {r[0]: float(r[1]) for r in rows}
    except Exception:
        logger.exception("_get_pre_closes_by_bars 查询失败 market=%s", market)
        return {}
    finally:
        session.close()


def _get_pre_closes(symbols: list[str], market: str) -> dict[str, float]:
    """批量查各标的昨收（V1 口径：优先最新日K的 pre_close 列）。

    pre_close 列导入自 V1 fact_bar_daily.pre_close，反映真实前一交易日收盘
    （即使最近两日收盘价相同也能算出正确今日涨跌）；列缺失时回退倒数第二根 close。
    """
    if not symbols:
        return {}
    out: dict[str, float] = {}
    session = get_session()
    try:
        subq = (
            session.query(
                orm.BarDailyModel.symbol,
                func.max(orm.BarDailyModel.trade_date).label("max_date"),
            )
            .filter(
                orm.BarDailyModel.symbol.in_(symbols),
                orm.BarDailyModel.market == market,
            )
            .group_by(orm.BarDailyModel.symbol)
            .subquery()
        )
        rows = (
            session.query(orm.BarDailyModel.symbol, orm.BarDailyModel.pre_close)
            .join(subq, (orm.BarDailyModel.symbol == subq.c.symbol) & (orm.BarDailyModel.trade_date == subq.c.max_date))
            .filter(orm.BarDailyModel.market == market, orm.BarDailyModel.pre_close.isnot(None))
            .all()
        )
        out = {r[0]: float(r[1]) for r in rows if r[1] is not None}
    except Exception:
        logger.exception("_get_pre_closes 查询失败 market=%s", market)
        out = {}
    finally:
        session.close()
    missing = [s for s in symbols if s not in out]
    if missing:
        out.update(_get_pre_closes_by_bars(missing, market))
    return out


def _get_realized_pnl_by_strategy(market: str) -> dict[str, float]:
    """各策略所有 CLOSED 持仓 pnl_amount 之和（V1 realized 口径）。"""
    session = get_session()
    try:
        rows = (
            session.query(
                orm.PositionModel.strategy_version_id,
                func.coalesce(func.sum(orm.PositionModel.pnl_amount), 0),
            )
            .filter(
                orm.PositionModel.market == market,
                orm.PositionModel.status == "CLOSED",
            )
            .group_by(orm.PositionModel.strategy_version_id)
            .all()
        )
        return {r[0]: float(r[1]) for r in rows}
    except Exception:
        logger.exception("_get_realized_pnl_by_strategy 查询失败 market=%s", market)
        return {}
    finally:
        session.close()


def _is_opened_today(opened_at: datetime | None, ref_date: date | None, tz) -> bool:
    """开仓时间（市场时区）的日期 == 参照交易日。无 opened_at/ref_date 视为非今日。"""
    if opened_at is None or ref_date is None:
        return False
    try:
        return opened_at.astimezone(tz).date() == ref_date
    except Exception:
        return False


def _lot_today_pnl(opened_today: bool, cur_price: float, open_price: float,
                   qty: float, pre_close: float | None) -> float | None:
    """V1 口径单笔今日盈亏：当日开仓=(现价-开仓价)*qty；否则=(现价-昨收)*qty。缺昨收→None。"""
    if opened_today:
        return (cur_price - open_price) * qty
    if pre_close is None:
        return None
    return (cur_price - pre_close) * qty


def _owned_instance_ids(current_user: User, db: PersistencePort) -> set[str] | None:
    """非 ADMIN 的自有运行实例 id 集合（owner 收敛用）；ADMIN 返回 None=不过滤。

    P2 合规红线：内置策略持仓/成交（user_strategy_id IS NULL）绝不外泄给非 ADMIN；
    非 ADMIN 只见自己实例产生的行级数据。复用 list_owned_instance_ids 口径。
    """
    if current_user.role == Role.ADMIN:
        return None
    from zhice_os_v2.application.services.user_strategy_service import (
        UserStrategyService,
    )

    return UserStrategyService(db).list_owned_instance_ids(current_user.user_id)


def _position_detail(position_id: str, db: PersistencePort, current_user: User) -> dict:
    """单持仓详情 payload（query/path 两形态共用）。

    按 position_id 直查（ports.get_position 不过滤状态）：CLOSED 持仓也可查详情。
    此前遍历 list_open_positions（写死 OPEN）→ 已平仓持仓详情恒 404。

    P2 owner 收敛：非 ADMIN 只可查自己实例的持仓；内置/他人持仓 → 404（不泄露存在性）。
    """
    p = db.get_position(position_id)
    if p is None:
        raise HTTPException(status_code=404, detail="position not found")
    owned = _owned_instance_ids(current_user, db)
    if owned is not None and (
        getattr(p, "user_strategy_id", None) is None or p.user_strategy_id not in owned
    ):
        raise HTTPException(status_code=404, detail="position not found")
    return {
        "position_id": p.position_id,
        "strategy_version_id": p.strategy_version_id,
        "market": p.market,
        "ts_code": p.ts_code,
        "direction": p.direction,
        "open_qty": str(p.open_qty),
        "open_price": str(p.open_price),
        "status": p.status,
    }


@router.get("")
def list_positions(market: str | None = None, strategy: str | None = None,
                   position_id: str | None = None,
                   db: PersistencePort = Depends(get_db),
                   current_user: User = Depends(deny_viewer)):
    # E11：query 形态单持仓详情（主）：GET /positions?position_id=…——CRYPTO 的
    # position_id 可含斜杠（ts_code 形如 XRP/USDT 拼进 id），path 模板 [^/]+
    # 匹配不了（%2F 路由同样不认），须走 query；原 /positions/{position_id}
    # 保留兼容无斜杠调用（同 B3 范式）。不给 position_id 时维持列表语义。
    if position_id:
        return _position_detail(position_id, db, current_user)
    # 持仓明细走 ORM 直查（需 pnl_amount/opened_at 与 strategy SQL 过滤），ports 仍为其他路由所用
    session = get_session()
    try:
        stmt = (
            session.query(orm.PositionModel)
            .filter(orm.PositionModel.status == "OPEN")
        )
        if market:
            stmt = stmt.filter(orm.PositionModel.market == market)
        if strategy:
            stmt = stmt.filter(orm.PositionModel.strategy_version_id == strategy)
        # P2 owner 收敛：非 ADMIN 只见自己实例的持仓；内置（user_strategy_id NULL）
        # 与他人持仓一律不返回（合规红线）。owned 为空 → in_([]) → 无行。
        owned = _owned_instance_ids(current_user, db)
        if owned is not None:
            stmt = stmt.filter(orm.PositionModel.user_strategy_id.in_(list(owned)))
        rows = stmt.order_by(orm.PositionModel.opened_at.desc().nullslast()).all()
        markets = sorted({r.market for r in rows})
    finally:
        session.close()

    # 按市场分组，批量查最新价 / 昨收 / 参照交易日
    by_market: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        if r.ts_code not in by_market[r.market]:
            by_market[r.market].append(r.ts_code)

    price_map: dict[str, float] = {}
    pre_close_map: dict[str, float] = {}
    ref_dates: dict[str, date | None] = {}
    trade_today: dict[str, date | None] = {}
    for mkt, symbols in by_market.items():
        price_map.update(_get_latest_prices(symbols, mkt))
        pre_close_map.update(_get_pre_closes(symbols, mkt))
        ref_dates[mkt] = _get_latest_trade_date(mkt)
        trade_today[mkt] = _current_trading_date(mkt)

    # 已实现盈亏：单策略视图只查该策略所在市场；全市场视图查各市场全部策略
    if market:
        realized_map = _get_realized_pnl_by_strategy(market)
    else:
        realized_map = {}
        for mkt in markets or []:
            realized_map.update(_get_realized_pnl_by_strategy(mkt))

    # 预计算每个策略账户的 total_equity = 初始 + 已实现 + 浮动（与 AccountStatsService.by_strategy 同口径）
    from zhice_os_v2.application.services.account_stats_service import _initial_capital

    # CN 复权成本重述（V1 valuation 语义）：cost_price *= f(建仓日)/f(参照日)。
    # 因子缺失 → 1.0 安全退化（非 CN 无因子恒 1.0，不影响其他市场）。
    restate_map: dict[str, float] = {}
    cn_rows = [p for p in rows if p.market == "CN" and p.opened_at]
    if cn_rows:
        try:
            from zhice_os_v2.application.services.adj_factor_service import (
                cost_restate_ratio,
            )
            for p in cn_rows:
                ref_d = ref_dates.get("CN")
                op_date = p.opened_at.astimezone(get_profile("CN").tz).date() \
                    if p.opened_at.tzinfo else p.opened_at.date()
                restate_map[p.position_id] = cost_restate_ratio(p.ts_code, op_date, ref_d)
        except Exception:
            restate_map = {}

    acct: dict[str, dict[str, float]] = defaultdict(lambda: {"cost": 0.0, "mv": 0.0})
    for p in rows:
        op, oq = float(p.open_price), float(p.open_qty)
        op = op * restate_map.get(p.position_id, 1.0)  # 重述后成本
        cur = price_map.get(p.ts_code, float(p.open_price))
        acct[p.strategy_version_id]["cost"] += op * oq
        acct[p.strategy_version_id]["mv"] += cur * oq
    equity_map: dict[str, float] = {}
    for sv_id, agg in acct.items():
        mkt = next((r.market for r in rows if r.strategy_version_id == sv_id), market or "CN")
        unrealized = agg["mv"] - agg["cost"]
        equity_map[sv_id] = _initial_capital(mkt) + realized_map.get(sv_id, 0.0) + unrealized

    result = []
    sv_names = AccountStatsService._strategy_names()
    for p in rows:
        open_price = float(p.open_price)
        open_qty = float(p.open_qty)
        current_price = price_map.get(p.ts_code, open_price)
        adj_open = open_price * restate_map.get(p.position_id, 1.0)  # 重述后成本价
        cost_amount = adj_open * open_qty
        market_value = current_price * open_qty
        # 浮盈按方向：多头 (现价-成本)，空头 (成本-现价)。此前恒按多头，空头符号会反。
        is_long = p.direction in ("BUY", "LONG")
        unit_pnl = (current_price - adj_open) if is_long else (adj_open - current_price)
        float_pnl = round(unit_pnl * open_qty, 2)
        float_pnl_pct = round(unit_pnl / adj_open * 100, 2) if adj_open else 0.0

        tz = get_profile(p.market).tz
        opened_today = _is_opened_today(p.opened_at, ref_dates.get(p.market), tz)
        # T+1 可用性用「当前交易日」判当日开仓，而非最新日K日期——盘中日K滞后一日会把昨仓
        # 误判为当日开仓致可用=0（本次修复）；今日盈亏仍沿用 opened_today（日K参照口径不变）。
        opened_this_td = _is_opened_today(p.opened_at, trade_today.get(p.market), tz)
        available_qty = 0 if (opened_this_td and not get_profile(p.market).same_day_sell_allowed) else open_qty
        today_pnl = _lot_today_pnl(opened_today, current_price, open_price, open_qty,
                                   pre_close_map.get(p.ts_code))
        equity = equity_map.get(p.strategy_version_id, 0.0)
        weight_pct = round(market_value / equity * 100, 2) if equity > 0 else 0.0

        is_crypto = p.market == "CRYPTO"
        qty_display = f"{open_qty:.4f}" if is_crypto else str(int(open_qty))
        price_display = f"{open_price:.4f}" if is_crypto else f"{open_price:.2f}"
        cur_price_display = f"{current_price:.4f}" if is_crypto else f"{current_price:.2f}"
        avail_display = f"{available_qty:.4f}" if is_crypto else str(int(available_qty))

        result.append({
            "position_id": p.position_id,
            "strategy_version_id": p.strategy_version_id,
            "strategy_name": sv_names.get(p.strategy_version_id, p.strategy_version_id),
            "market": p.market,
            "ts_code": p.ts_code,
            "direction": p.direction,
            "open_qty": qty_display,
            "open_price": price_display,
            "current_price": cur_price_display,
            "float_pnl": float_pnl,
            "float_pnl_pct": float_pnl_pct,
            "status": p.status,
            # V1 get_positions_detail 口径新增字段
            "available_qty": avail_display,
            "cost_amount": round(cost_amount, 2),
            "market_value": round(market_value, 2),
            "today_pnl": round(today_pnl, 2) if today_pnl is not None else None,
            "weight_pct": weight_pct,
            "opened_at": p.opened_at.isoformat() if p.opened_at else None,
        })
    return result


class CloseBody(BaseModel):
    """管理端平仓请求（P0-1：禁止任意价、禁止无校验平仓）。

    - close_price 已废弃：传非空值会被 400 拒绝；成交价一律取最新行情价。
    - account_id / position_ids 可选限定范围；不限定 = 该市场全部 OPEN 持仓，
      但逐笔过 T+1/停牌/跌停守卫，任一不可卖即整单拒绝（fail-closed，不部分平仓）。
    """

    market: str
    account_id: str | None = None
    position_ids: list[str] | None = None
    close_price: float | None = None  # 兼容旧字段；非空即拒绝


@router.post("/close")
def close_all_positions(
    body: CloseBody,
    db: PersistencePort = Depends(get_db),
    _: User = Depends(require_roles(Role.ADMIN, Role.TRADER)),
):
    """管理端平仓：逐笔通过统一卖出守卫后按最新行情价平仓。

    守卫复用 domain/execution/t1_guard.py（CN 当日开仓不可卖、可卖量、停牌/跌停
    fail-closed）；任一持仓不可卖 → 409 整单拒绝、不写任何仓位/流水。
    """
    from zhice_os_v2.application.services.workflow_service import WorkflowService
    from zhice_os_v2.domain.execution.t1_guard import check_sell
    from zhice_os_v2.domain.ontology.objects import utc_now

    positions = list(db.list_open_positions(market=body.market))
    if body.account_id is not None:
        positions = [
            p for p in positions
            if getattr(p, "account_id", None) == body.account_id
        ]
    if body.position_ids is not None:
        wanted = set(body.position_ids)
        positions = [p for p in positions if p.position_id in wanted]
    if not positions:
        return {"closed": [], "count": 0, "market": body.market}
    if body.close_price is not None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "positions.close_price_not_allowed",
                "message": "禁止任意价平仓：close_price 已废弃，成交价一律取最新行情价",
            },
        )

    ts_codes = sorted({p.ts_code for p in positions})
    try:
        prices = _get_latest_prices(ts_codes, body.market)
        pre_closes = _get_pre_closes(ts_codes, body.market)
    except Exception as exc:  # noqa: BLE001 - 行情不可用必须 fail-closed
        logger.exception("positions/close 行情获取失败 market=%s", body.market)
        raise HTTPException(
            status_code=503,
            detail={"code": "positions.market_data_unavailable"},
        ) from exc

    now = utc_now()
    blocked: list[dict] = []
    close_price_by_pos: dict[str, Decimal] = {}
    for p in positions:
        price = prices.get(p.ts_code)
        decision = check_sell(
            market=body.market,
            ts_code=p.ts_code,
            qty=p.open_qty,
            lots=[p],
            price=price,
            pre_close=pre_closes.get(p.ts_code),
            now=now,
        )
        if not decision.ok:
            blocked.append({
                "position_id": p.position_id,
                "ts_code": p.ts_code,
                "reason": decision.reason,
                "available_qty": str(decision.available_qty),
            })
            continue
        close_price_by_pos[p.position_id] = Decimal(str(price))
    if blocked:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "positions.close_blocked",
                "market": body.market,
                "blocked": blocked,
                "message": "存在不可卖持仓，已整单拒绝（未平任何仓位）",
            },
        )

    replay_service = ReplayService(db)
    learning_service = LearningService(db)
    workflow = WorkflowService(db)
    _long = {"BUY", "LONG"}
    closed = []
    for p in positions:
        close_price = close_price_by_pos[p.position_id]
        p.status = PositionStatus.CLOSED
        p.close_qty = p.open_qty
        p.close_price = close_price
        p.closed_at = utc_now()
        # 已实现盈亏：按方向计算并落库（此前不写 pnl_amount，导致这些平仓 realized 静默计 0）
        unit = (
            (close_price - p.open_price)
            if p.direction in _long
            else (p.open_price - close_price)
        )
        p.pnl_amount = unit * p.open_qty
        db.save_position(p)
        # 平仓成交落库（Bug B 修复，603311 案：此前从不写 SELL 流水，9-02 一键
        # 平仓 11 笔交易记录缺失）。内存后端 _record_fill 自行跳过，语义不变。
        workflow._record_fill(
            market=p.market,
            ts_code=p.ts_code,
            side="SELL",
            qty=p.close_qty or p.open_qty,
            price=close_price,
            position=p,
            fill_time=p.closed_at,
            sell_reason="人工平仓：管理端一键平仓（T+1守卫通过，最新行情价）",
            realized_pnl=p.pnl_amount,
            exit_kind="MANUAL",
        )
        replay = replay_service.build_replay_case(p)
        if replay:
            learning_service.record_from_replay(replay)
        closed.append(p.ts_code)
    return {
        "closed": closed,
        "count": len(closed),
        "market": body.market,
        "price_source": "latest_market",
    }


@router.get("/{position_id}")
def get_position(position_id: str, db: PersistencePort = Depends(get_db),
                 current_user: User = Depends(deny_viewer)):
    # path 兼容形态（无斜杠 position_id）；含斜杠请走 GET /positions?position_id=
    return _position_detail(position_id, db, current_user)
