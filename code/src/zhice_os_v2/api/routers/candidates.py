"""候选池 API（V1 candidates 对齐）。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, distinct, func

from zhice_os_v2.api.dependencies import get_current_user

logger = logging.getLogger(__name__)

from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session

router = APIRouter(prefix="/candidates", tags=["candidates"], dependencies=[Depends(get_current_user)])


def _strategy_names() -> dict[str, str]:
    """strategy_version_id → 策略名（V1 名称，去 [V1] 前缀）。

    与 AccountStatsService._strategy_names 相同口径（复制以避免跨服务依赖）。
    """
    session = get_session()
    try:
        rows = (
            session.query(orm.StrategyVersionModel.strategy_version_id, orm.StrategyModel.strategy_name)
            .join(orm.StrategyModel, orm.StrategyModel.strategy_id == orm.StrategyVersionModel.strategy_id)
            .all()
        )
        out: dict[str, str] = {}
        for sv_id, name in rows:
            n = (name or "").strip()
            if n.startswith("[V1] "):
                n = n[5:]
            out[sv_id] = n
        return out
    except Exception:
        logger.exception("_strategy_names 查询失败")
        return {}
    finally:
        session.close()


@router.get("/pools")
def list_pools(market: str = "CN", date: str | None = None, strategy: str | None = None):
    """候选池批次列表（最新在前）。

    默认取该市场最新一个交易日的全部池；date 参数取指定日；
    strategy 参数按 strategy_version_id 过滤。
    """
    session = get_session()
    try:
        q = session.query(orm.CandidatePoolModel).filter(orm.CandidatePoolModel.market == market)
        if date:
            q = q.filter(orm.CandidatePoolModel.pool_date == date)
        else:
            # 按 strategy_version 各取其最新 pool_date：registry(每日盘前) 与 signal(信号驱动/
            # 按市场时段) 产池节奏不同步，用全局 max(pool_date) 会把落后一天的 signal 池挡在
            # 最新日外，导致 US 等只剩 registry(趋势动量)。改为每 sv 各取自身最新日，两类池并存。
            sub = (
                session.query(
                    orm.CandidatePoolModel.strategy_version_id.label("sv"),
                    func.max(orm.CandidatePoolModel.pool_date).label("md"),
                )
                .filter(orm.CandidatePoolModel.market == market)
                .group_by(orm.CandidatePoolModel.strategy_version_id)
                .subquery()
            )
            q = q.join(
                sub,
                and_(
                    orm.CandidatePoolModel.strategy_version_id == sub.c.sv,
                    orm.CandidatePoolModel.pool_date == sub.c.md,
                ),
            )
        if strategy:
            q = q.filter(orm.CandidatePoolModel.strategy_version_id == strategy)
        pools = (
            q.order_by(orm.CandidatePoolModel.pool_date.desc(), orm.CandidatePoolModel.batch_no.desc())
            .all()
        )
    except Exception:
        # 方案A：返回错误码而非中文，前端 errors.json 按语言翻译
        raise HTTPException(status_code=500, detail={"code": "candidate.pools_query_failed"})
    finally:
        session.close()  # 异常路径也关闭，避免连接泄漏

    names = _strategy_names()
    # 真实候选数以 v2_candidate_asset 去重为准；candidate_count 仅作声明值保留。
    actual = {}
    updated = {}
    try:
        session = get_session()
        pool_ids = [p.pool_id for p in pools]
        if pool_ids:
            for pid, n in (
                session.query(
                    orm.CandidateAssetModel.pool_id,
                    func.count(distinct(orm.CandidateAssetModel.ts_code)),
                )
                .filter(orm.CandidateAssetModel.pool_id.in_(pool_ids))
                .group_by(orm.CandidateAssetModel.pool_id)
                .all()
            ):
                actual[pid] = int(n or 0)
            for pid, ts in (
                session.query(
                    orm.CandidateAssetModel.pool_id,
                    func.max(orm.CandidateAssetModel.created_at),
                )
                .filter(orm.CandidateAssetModel.pool_id.in_(pool_ids))
                .group_by(orm.CandidateAssetModel.pool_id)
                .all()
            ):
                updated[pid] = ts
    except Exception:
        logger.exception("candidate actual count 查询失败")
    finally:
        try:
            session.close()
        except Exception:
            pass
    out = []
    for p in pools:
        declared = int(p.candidate_count or 0)
        real = actual.get(p.pool_id)
        out.append({
            "pool_id": p.pool_id,
            "strategy_version_id": p.strategy_version_id,
            "strategy_name": names.get(p.strategy_version_id, p.strategy_version_id),
            "pool_date": p.pool_date.isoformat() if p.pool_date else None,
            "pool_type": p.pool_type,
            "candidate_count": real if real is not None else declared,
            "declared_candidate_count": declared,
            "updated_at": (updated.get(p.pool_id) or p.created_at).isoformat() if (updated.get(p.pool_id) or p.created_at) else None,
            "status": p.status,
        })
    return out


@router.get("/pools/{pool_id}/assets")
def pool_assets(pool_id: str, limit: int = 100):
    """单个池的候选明细（评分降序）。

    ref_price 从 v2_trade_plan（同 ts_code + 池策略最新一条）LEFT JOIN，无则 None。
    """
    pool = None
    assets: list = []
    ref_prices: dict[str, float] = {}
    try:
        session = get_session()
        pool = (
            session.query(orm.CandidatePoolModel)
            .filter(orm.CandidatePoolModel.pool_id == pool_id)
            .first()
        )
        if pool is None:
            session.close()
            raise HTTPException(status_code=404, detail={"code": "candidate.pool_not_found"})
        assets = (
            session.query(orm.CandidateAssetModel)
            .filter(orm.CandidateAssetModel.pool_id == pool_id)
            .order_by(orm.CandidateAssetModel.candidate_score.desc().nullslast())
            .limit(limit)
            .all()
        )
        symbols = [a.ts_code for a in assets]

        # 同 ts_code + 策略最新一条交易计划的 ref_price
        if symbols:
            subq = (
                session.query(
                    orm.TradePlanModel.ts_code,
                    func.max(orm.TradePlanModel.created_at).label("max_at"),
                )
                .filter(
                    orm.TradePlanModel.ts_code.in_(symbols),
                    orm.TradePlanModel.strategy_version_id == pool.strategy_version_id,
                )
                .group_by(orm.TradePlanModel.ts_code)
                .subquery()
            )
            rows = (
                session.query(orm.TradePlanModel.ts_code, orm.TradePlanModel.ref_price)
                .join(
                    subq,
                    (orm.TradePlanModel.ts_code == subq.c.ts_code)
                    & (orm.TradePlanModel.created_at == subq.c.max_at),
                )
                .all()
            )
            for ts_code, ref in rows:
                if ref is not None:
                    ref_prices[ts_code] = float(ref)
        session.close()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail={"code": "candidate.details_query_failed"})

    return [
        {
            "candidate_id": a.candidate_id,
            "ts_code": a.ts_code,
            "candidate_score": float(a.candidate_score) if a.candidate_score is not None else None,
            "candidate_reason": a.candidate_reason,
            "status": a.status,
            "ref_price": ref_prices.get(a.ts_code),
        }
        for a in assets
    ]
