"""账户深度 API（V1 accounts 对齐）。

  - /risk-summary 各策略账户风险摘要（仓位 / 最大单标的权重 / 今日盈亏 / 单日亏损红线）
  - /attribution 账户（策略）维度归因：v2_attribution_record join v2_replay 的
    strategy_version_id 按 6 归因维度分组求和
"""
from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy import func

from zhice_os_v2.api.dependencies import deny_viewer, get_current_user, get_db
from zhice_os_v2.application.services.account_stats_service import (
    AccountStatsService,
    _initial_capital,
)
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

router = APIRouter(prefix="/accounts-ext", tags=["accounts-ext"], dependencies=[Depends(get_current_user)])

# 6 个归因维度（与 governance_ext 同口径）
ATTRIBUTION_DIMS = ["SELECTION", "ENTRY", "EXIT", "MARKET", "RISK", "BEHAVIOR"]

# 单日亏损红线（初始资金 %，与治理红线 DAILY_LOSS_LIMIT_PCT 同口径）
DAILY_LOSS_LIMIT_PCT = 2.0


@router.get("/risk-summary")
def risk_summary(
    market: str = "CN",
    db: PersistencePort = Depends(get_db),
    _guard=Depends(deny_viewer),
):
    """账户风险摘要：各策略账户仓位/最大单标的权重/今日盈亏/单日亏损红线。"""
    svc = AccountStatsService(db)
    accounts = svc.by_strategy(market)
    positions = db.list_open_positions(market=market)
    prices = svc._latest_prices(list({p.ts_code for p in positions}), market)

    groups: dict[str, list] = defaultdict(list)
    for p in positions:
        groups[p.strategy_version_id].append(p)

    out = []
    for a in accounts:
        sv_id = a["strategy_version_id"]
        # 单标的权重 = 账户内同一标的市值合计 / 账户权益（与 /positions 的 weight_pct 同口径）
        by_sym: dict[str, float] = defaultdict(float)
        for l in groups.get(sv_id, []):
            cur = prices.get(l.ts_code, float(l.open_price))
            by_sym[l.ts_code] += cur * float(l.open_qty)
        max_single_weight = max(
            (mv / a["total_equity"] * 100 for mv in by_sym.values() if a["total_equity"] > 0),
            default=0.0,
        )
        limit_amount = _initial_capital(market) * DAILY_LOSS_LIMIT_PCT / 100
        out.append(
            {
                "strategy_version_id": sv_id,
                "strategy_name": a["strategy_name"],
                "position_ratio": a["position_ratio"],
                "max_single_weight": round(max_single_weight, 2),
                "today_pnl": a["today_pnl"],
                "daily_loss_limit_pct": DAILY_LOSS_LIMIT_PCT,
                "breach_daily_loss": a["today_pnl"] < -limit_amount,
            }
        )
    return out


@router.get("/attribution")
def account_attribution(market: str = "CN", _guard=Depends(deny_viewer)):
    """账户（策略）维度归因：已实现盈亏按 6 归因维度拆解。"""
    session = get_session()
    try:
        rows = (
            session.query(
                orm.ReplayModel.strategy_version_id,
                orm.AttributionRecordModel.dimension,
                func.sum(orm.AttributionRecordModel.pnl_amount),
            )
            .join(
                orm.AttributionRecordModel,
                orm.AttributionRecordModel.replay_id == orm.ReplayModel.replay_id,
            )
            .filter(
                orm.AttributionRecordModel.market == market,
            )
            .group_by(orm.ReplayModel.strategy_version_id, orm.AttributionRecordModel.dimension)
            .all()
        )
        all_dims = [
            r[0]
            for r in (
                session.query(orm.AttributionRecordModel.dimension)
                .filter(orm.AttributionRecordModel.market == market)
                .distinct()
                .all()
            )
        ]
    finally:
        session.close()

    dims = [d for d in ATTRIBUTION_DIMS if d in all_dims] + sorted(
        d for d in all_dims if d not in ATTRIBUTION_DIMS
    )
    names = AccountStatsService._strategy_names()

    pnl: dict[str, dict[str, float]] = defaultdict(lambda: {d: 0.0 for d in dims})
    total: dict[str, float] = defaultdict(float)
    for sv_id, dim, amount in rows:
        v = float(amount or 0)
        pnl[sv_id][dim] = pnl[sv_id].get(dim, 0.0) + v
        total[sv_id] += v

    return [
        {
            "strategy_version_id": sv_id,
            "strategy_name": names.get(sv_id, sv_id),
            "dimensions": pnl[sv_id],
            "total_pnl": round(total[sv_id], 2),
        }
        for sv_id in sorted(pnl, key=lambda s: total[s], reverse=True)
    ]
