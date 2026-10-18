"""成交明细 API：v2_fill_record 列表（过滤 + 分页）。

GET /api/v1/trades —— 登录（router 级 get_current_user）+ deny_viewer（VIEWER 403）。
P2 owner 收敛：非 ADMIN 只见自己实例产生的成交；内置策略成交（沿 fill→execution→
request→intent 链，intent.user_strategy_id IS NULL）对非 ADMIN 一律隐藏（合规红线，
盈亏结果另走聚合端点）。ADMIN 全量可见。
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Query

from zhice_os_v2.api.dependencies import deny_viewer, get_current_user, get_db
from zhice_os_v2.domain.security.user import Role, User
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

router = APIRouter(
    prefix="/trades", tags=["trades"], dependencies=[Depends(get_current_user)]
)


@router.get("")
def list_trades(
    market: str | None = None,
    ts_code: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: PersistencePort = Depends(get_db),
    current_user: User = Depends(deny_viewer),
):
    """成交明细列表：按 fill_time 倒序，支持 market/ts_code/日期区间过滤与分页。

    start/end_date 为闭区间自然日（[start 00:00, end+1d 00:00)）；fill_time 缺失
    的行排在最后（nullslast）。market 不过枚举校验：传入无数据的市场自然返回空。

    P2：非 ADMIN 经 fill→execution→request→intent join，按 intent.user_strategy_id
    收敛到自有实例；内置/无归属成交（NULL / inner join 丢弃）不返回。
    """
    session = get_session()
    try:
        q = session.query(orm.FillRecordModel)
        # owner 收敛（非 ADMIN）：join 到 order_intent 取 user_strategy_id，限本人实例。
        if current_user.role != Role.ADMIN:
            from zhice_os_v2.application.services.user_strategy_service import (
                UserStrategyService,
            )

            owned = UserStrategyService(db).list_owned_instance_ids(
                current_user.user_id
            )
            q = (
                q.join(
                    orm.ExecutionRecordModel,
                    orm.FillRecordModel.execution_id
                    == orm.ExecutionRecordModel.execution_id,
                )
                .join(
                    orm.OrderRequestModel,
                    orm.ExecutionRecordModel.request_id
                    == orm.OrderRequestModel.request_id,
                )
                .join(
                    orm.OrderIntentModel,
                    orm.OrderRequestModel.intent_id == orm.OrderIntentModel.intent_id,
                )
                .filter(orm.OrderIntentModel.user_strategy_id.in_(list(owned)))
            )
        if market:
            q = q.filter(orm.FillRecordModel.market == market)
        if ts_code:
            q = q.filter(orm.FillRecordModel.ts_code == ts_code)
        if start_date is not None:
            q = q.filter(orm.FillRecordModel.fill_time >= datetime.combine(start_date, time.min))
        if end_date is not None:
            q = q.filter(
                orm.FillRecordModel.fill_time
                < datetime.combine(end_date, time.min) + timedelta(days=1)
            )
        total = q.count()
        rows = (
            q.order_by(
                orm.FillRecordModel.fill_time.desc().nullslast(),
                orm.FillRecordModel.id.desc(),
            )
            .offset(offset)
            .limit(limit)
            .all()
        )
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": [
                {
                    "fill_id": f.fill_id,
                    "market": f.market,
                    "ts_code": f.ts_code,
                    "fill_price": float(f.fill_price) if f.fill_price is not None else None,
                    "fill_qty": float(f.fill_qty) if f.fill_qty is not None else None,
                    "fill_amount": float(f.fill_amount) if f.fill_amount is not None else None,
                    "fill_time": f.fill_time.isoformat() if f.fill_time else None,
                    "realized_pnl": float(f.realized_pnl)
                    if f.realized_pnl is not None
                    else None,
                    "sell_reason": f.sell_reason,
                }
                for f in rows
            ],
        }
    finally:
        session.close()
