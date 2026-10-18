"""策略详情 API：单策略的账户统计/持仓/成交记录/绩效一站式视图。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, func

from zhice_os_v2.api.dependencies import get_current_user, get_db, strategy_visible_to
from zhice_os_v2.api.routers.positions import _get_latest_prices
from zhice_os_v2.application.services.account_stats_service import (
    AccountStatsService,
    _initial_capital,
)
from zhice_os_v2.domain.market.registry import SUPPORTED_MARKETS
from zhice_os_v2.domain.security.user import Role, User
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

router = APIRouter(
    prefix="/strategies", tags=["strategy-detail"], dependencies=[Depends(get_current_user)]
)

_TRADE_LIMIT = 200


def _market_from_strategy_version_id(strategy_version_id: str, fallback: str) -> str:
    """v1-cn-sv-4 -> CN；解析失败回退到查询参数。"""
    parts = strategy_version_id.split("-")
    if len(parts) >= 2 and parts[1].isalpha():
        return parts[1].upper()
    return fallback


def _resolve_version_id(id_or_sv: str) -> str:
    """兼容前端传 strategy_id（v1-crypto-strat-11）或 strategy_version_id（v1-crypto-sv-3）。

    管理页 Link 到 /strategies/:id 传的是 strategy_id，但本接口的 positions/trades/account
    都按 strategy_version_id 过滤——若不规整，strat-11 永远匹配不到 sv-3 → 详情全空。
    传入本身是 sv 则原样返回；否则当 strategy_id 查其版本（ACTIVE 优先、新版本优先）。
    """
    session = get_session()
    try:
        is_sv = (
            session.query(orm.StrategyVersionModel.strategy_version_id)
            .filter(orm.StrategyVersionModel.strategy_version_id == id_or_sv)
            .first()
        )
        if is_sv:
            return id_or_sv
        row = (
            session.query(orm.StrategyVersionModel.strategy_version_id)
            .filter(orm.StrategyVersionModel.strategy_id == id_or_sv)
            .order_by(
                (orm.StrategyVersionModel.status == "ACTIVE").desc(),
                orm.StrategyVersionModel.id.desc(),
            )
            .first()
        )
        return row[0] if row else id_or_sv
    finally:
        session.close()


def _ensure_strategy_visible(id_or_sv: str, db: PersistencePort, current_user: User) -> None:
    """可见性门（Phase D 终审 Important #1，口径同 eaeafb1）：私有策略仅 owner/ADMIN。

    兼容两种入参形态：strategy_version_id（查 version→strategy）或直传
    strategy_id（查 strategy）。他人私有 → 404（不泄露存在性）；id 未知（无版本
    也无策略行）保持旧行为（空账户 200）——本修复只针对"私有泄露"口径。
    注：enforced 阶段 detail 持仓/成交按 strategy_version_id 聚合会跨用户实例混显，
    属后续按 user_strategy 隔离的门禁（见 runbook 阶段 D），不在本口径内。
    """
    version = db.get_strategy_version(id_or_sv)
    strategy = db.get_strategy(version.strategy_id) if version else db.get_strategy(id_or_sv)
    if strategy is not None and not strategy_visible_to(strategy, current_user):
        raise HTTPException(status_code=404, detail="strategy version not found")


@router.get("/{strategy_version_id}/detail")
def strategy_detail(
    strategy_version_id: str,
    market: str = "CN",
    db: PersistencePort = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """单策略详情聚合。market 参数冗余保护（strategy_version_id 已含市场前缀 v1-cn-sv-X，但保留参数兼容）。"""
    _ensure_strategy_visible(strategy_version_id, db, current_user)
    strategy_version_id = _resolve_version_id(strategy_version_id)  # 兼容前端传 strategy_id
    market = _market_from_strategy_version_id(strategy_version_id, market)
    # sv 前缀解析不出的市场（如 sv-unknown → UNKNOWN）此前流入 get_profile 抛
    # ValueError → 500；与邻端点 _ensure_strategy_visible 的"未知 id → 404"口径
    # 一致：404（不泄露存在性），绝不 500。
    if market not in SUPPORTED_MARKETS:
        raise HTTPException(status_code=404, detail={"code": "strategy.not_found"})

    # 1. 账户统计：复用 AccountStatsService.by_strategy 的单条逻辑，无持仓则返回初始资金账户
    initial = _initial_capital(market)
    accounts = AccountStatsService(db).by_strategy(market)
    account = next((a for a in accounts if a["strategy_version_id"] == strategy_version_id), None)
    if account is None:
        account = {
            "strategy_version_id": strategy_version_id,
            "market": market,
            "initial_capital": round(initial, 2),
            "available_cash": round(initial, 2),
            "market_value": 0.0,
            "total_equity": round(initial, 2),
            "unrealized_pnl": 0.0,
            "unrealized_pnl_pct": 0.0,
            "position_ratio": 0.0,
            "position_count": 0,
        }

    session = get_session()
    try:
        # 2. 持仓：该策略的 OPEN position，带现价和浮盈
        pos_rows = (
            session.query(orm.PositionModel)
            .filter(
                orm.PositionModel.strategy_version_id == strategy_version_id,
                orm.PositionModel.status == "OPEN",
            )
            .order_by(orm.PositionModel.opened_at.desc())
            .all()
        )
        price_map = _get_latest_prices(
            [p.ts_code for p in pos_rows], pos_rows[0].market if pos_rows else market
        )

        positions = []
        for p in pos_rows:
            open_price = float(p.open_price)
            open_qty = float(p.open_qty)
            current_price = price_map.get(p.ts_code, open_price)
            float_pnl = (current_price - open_price) * open_qty
            positions.append(
                {
                    "position_id": p.position_id,
                    "ts_code": p.ts_code,
                    "direction": p.direction,
                    "open_qty": open_qty,
                    "open_price": open_price,
                    "current_price": current_price,
                    "float_pnl": round(float_pnl, 2),
                    "float_pnl_pct": round((current_price / open_price - 1) * 100, 2)
                    if open_price
                    else 0.0,
                    "opened_at": p.opened_at.isoformat() if p.opened_at else None,
                }
            )

        # 3. 成交记录：fill -> execution -> order_request -> order_intent（inner join 自动跳过关联不上的 fill）
        #    reason：优先 approval（同标的同日）；fallback 候选池 candidate_reason（同策略同标的最近池）
        #    realized_pnl：直接读 v2_fill_record.realized_pnl（回填列；BUY 行为 null）
        approval_reason = (
            session.query(
                orm.ApprovalRequestModel.ts_code.label("apr_ts_code"),
                func.date(orm.ApprovalRequestModel.created_at).label("apr_day"),
                orm.ApprovalRequestModel.reason.label("apr_reason"),
            )
            .filter(
                orm.ApprovalRequestModel.market == market,
                orm.ApprovalRequestModel.reason.isnot(None),
            )
            .distinct(
                orm.ApprovalRequestModel.ts_code, func.date(orm.ApprovalRequestModel.created_at)
            )
            .subquery()
        )
        cand_reason = (
            session.query(
                orm.CandidateAssetModel.ts_code.label("cand_ts"),
                orm.CandidateAssetModel.candidate_reason.label("cand_reason"),
                orm.CandidatePoolModel.pool_date.label("cand_date"),
            )
            .join(
                orm.CandidatePoolModel,
                orm.CandidateAssetModel.pool_id == orm.CandidatePoolModel.pool_id,
            )
            .filter(
                orm.CandidatePoolModel.market == market,
                orm.CandidatePoolModel.strategy_version_id == strategy_version_id,
                orm.CandidateAssetModel.candidate_reason.isnot(None),
            )
            .distinct(orm.CandidateAssetModel.ts_code)
            .order_by(orm.CandidateAssetModel.ts_code, orm.CandidatePoolModel.pool_date.desc())
            .subquery()
        )
        trade_rows = (
            session.query(
                orm.FillRecordModel,
                orm.OrderIntentModel.intent_side,
                approval_reason.c.apr_reason,
                cand_reason.c.cand_reason,
            )
            .join(
                orm.ExecutionRecordModel,
                orm.FillRecordModel.execution_id == orm.ExecutionRecordModel.execution_id,
            )
            .join(
                orm.OrderRequestModel,
                orm.ExecutionRecordModel.request_id == orm.OrderRequestModel.request_id,
            )
            .join(
                orm.OrderIntentModel,
                orm.OrderRequestModel.intent_id == orm.OrderIntentModel.intent_id,
            )
            .outerjoin(
                approval_reason,
                (approval_reason.c.apr_ts_code == orm.FillRecordModel.ts_code)
                & (approval_reason.c.apr_day == func.date(orm.FillRecordModel.fill_time)),
            )
            .outerjoin(
                cand_reason,
                cand_reason.c.cand_ts == orm.FillRecordModel.ts_code,
            )
            .filter(orm.OrderIntentModel.strategy_version_id == strategy_version_id)
            .order_by(orm.FillRecordModel.fill_time.desc().nullslast())
            .limit(_TRADE_LIMIT)
            .all()
        )
        trades = [
            {
                "fill_id": f.fill_id,
                "ts_code": f.ts_code,
                "side": side or "",
                "price": float(f.fill_price) if f.fill_price is not None else None,
                "qty": float(f.fill_qty) if f.fill_qty is not None else None,
                "amount": float(f.fill_amount) if f.fill_amount is not None else None,
                "fill_time": f.fill_time.isoformat() if f.fill_time else None,
                "realized_pnl": float(f.realized_pnl)
                if getattr(f, "realized_pnl", None) is not None
                else None,
                # 卖出用卖出理由（sell_reason），买入用选股理由（approval/candidate）
                "reason": (
                    getattr(f, "sell_reason", None)
                    if side == "SELL" and getattr(f, "sell_reason", None)
                    else (apr or cand or None)
                ),
            }
            for f, side, apr, cand in trade_rows
        ]

        # 4. 绩效：该策略 replay 聚合（pnl>0 为 win，<=0 为 loss）
        total_replays, wins, total_pnl = (
            session.query(
                func.count(orm.ReplayModel.id),
                func.coalesce(func.sum(case((orm.ReplayModel.pnl > 0, 1), else_=0)), 0),
                func.coalesce(func.sum(orm.ReplayModel.pnl), 0),
            )
            .filter(
                orm.ReplayModel.strategy_version_id == strategy_version_id,
            )
            .one()
        )
        total_replays = int(total_replays or 0)
        wins = int(wins or 0)
        total_pnl = float(total_pnl or 0)
        performance = {
            "total_replays": total_replays,
            "wins": wins,
            "losses": total_replays - wins,
            "win_rate": round(wins / total_replays, 4) if total_replays else 0.0,
            "total_pnl": round(total_pnl, 2),
            "avg_pnl": round(total_pnl / total_replays, 2) if total_replays else 0.0,
        }
    finally:
        session.close()

    # 内置/他人策略脱敏（P2 合规红线）：仅 owner 本人与 ADMIN 可见行级持仓/成交明细；
    # 内置策略（owner_user_id is None）对所有非 ADMIN 一律隐藏——免费/会员/订阅者都
    # 只能看聚合 account/performance（盈亏结果），看不到持仓与成交明细。已涵盖 VIEWER。
    _ver = db.get_strategy_version(strategy_version_id)
    _strat = (
        db.get_strategy(_ver.strategy_id) if _ver else db.get_strategy(strategy_version_id)
    )
    _owner_id = _strat.owner_user_id if _strat else None
    if current_user.role != Role.ADMIN and _owner_id != current_user.user_id:
        positions = []
        trades = []

    return {
        "strategy_version_id": strategy_version_id,
        "market": market,
        "account": account,
        "positions": positions,
        "trades": trades,
        "performance": performance,
    }
