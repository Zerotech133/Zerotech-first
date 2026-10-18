"""多策略冲突裁决（V1 multi_strategy/conflict_resolver 移植，工作流侧）。

V1 Step6.5：盘前多策略计划全部生成后、风控审查前，对同 ts_code 被多个
策略版本选中的计划做裁决——子账户体制下同股共持是 A/B 对照目的、跨策略
不算冲突；冲突域 = 同 ts_code 且不同 strategy_version_id 的活跃计划组。

默认 PRIORITY 策略：保留策略等级最高者（PROD>STAGING>DRAFT，V1 _STATUS_PRIORITY），
同级比信心分（confidence_level）。败者 plan_status='CANCELLED'。

与 api/routers/multi_strategy.py 的分工：那是人工 API（支持三模式/自定义优先序），
本函数是盘前自动裁决（固定 PRIORITY + 等级序），供 scheduler 进程调用。
"""
from __future__ import annotations

import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

ACTIVE_PLAN_STATUSES = ("PENDING", "APPROVED")

# V1 _STATUS_PRIORITY：数值小者优先（PROD 生产 > STAGING 灰度 > DRAFT 草稿）
_STATUS_PRIORITY = {"PROD": 0, "ACTIVE": 0, "STAGING": 1, "DRAFT": 2, "PENDING_REVIEW": 3}


def resolve_plan_conflicts(market: str) -> dict:
    """盘前 Step6.5：裁决当日同标的多策略活跃计划，败者 CANCELLED。

    返回 {date, conflict_groups, cancelled_plan_ids}。无冲突 → cancelled 空。
    """
    from sqlalchemy import func

    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session

    session = get_session()
    try:
        day = (
            session.query(func.max(func.date(orm.TradePlanModel.created_at)))
            .filter(orm.TradePlanModel.market == market,
                    orm.TradePlanModel.plan_status.in_(ACTIVE_PLAN_STATUSES))
            .scalar()
        )
        if day is None:
            return {"date": None, "conflict_groups": 0, "cancelled_plan_ids": [],
                    "reverted_approvals": 0}
        rows = (
            session.query(orm.TradePlanModel)
            .filter(orm.TradePlanModel.market == market,
                    orm.TradePlanModel.plan_status.in_(ACTIVE_PLAN_STATUSES),
                    func.date(orm.TradePlanModel.created_at) == day)
            .all()
        )
        # 策略版本等级（promotion 状态）
        sv_rank: dict[str, int] = {}
        for sv, status in (
            session.query(orm.StrategyVersionModel.strategy_version_id,
                          orm.StrategyVersionModel.status).all()
        ):
            sv_rank[sv] = _STATUS_PRIORITY.get(str(status), 2)

        from zhice_os_v2.domain.ontology.objects import utc_now

        groups: dict[str, list] = defaultdict(list)
        for p in rows:
            groups[p.ts_code].append(p)
        cancelled: list[str] = []
        reverted = 0
        n_groups = 0
        for _ts, plans in groups.items():
            if len({p.strategy_version_id for p in plans}) <= 1:
                continue
            n_groups += 1
            ranked = sorted(
                plans,
                key=lambda p: (sv_rank.get(p.strategy_version_id, 2),
                               -float(p.confidence_level or 0.0)),
            )
            for loser in ranked[1:]:
                loser.plan_status = "CANCELLED"
                cancelled.append(loser.plan_id)
                # 重做B第一重纵深：败者计划 CANCELLED 时，一并回退其授权。plan_id 已唯一
                # 带 sv → order_intent_id == loser.plan_id 只命中败者授权、不误伤同标的
                # 赢家（赢家 plan_id 不同）。PENDING/APPROVED → CANCELLED，镜像 expire
                # 写 decided_at；否则败者被裁决但授权仍 APPROVED → 盘中入场循环照样成交。
                for apr in (
                    session.query(orm.ApprovalRequestModel)
                    .filter(orm.ApprovalRequestModel.order_intent_id == loser.plan_id,
                            orm.ApprovalRequestModel.status.in_(("PENDING", "APPROVED")))
                    .all()
                ):
                    apr.status = "CANCELLED"
                    apr.decided_at = utc_now()
                    reverted += 1
        if cancelled:
            session.commit()
        result = {"date": str(day), "conflict_groups": n_groups,
                  "cancelled_plan_ids": cancelled, "reverted_approvals": reverted}
        if n_groups:
            logger.info("resolve_plan_conflicts market=%s %s", market,
                        {"groups": n_groups, "cancelled": len(cancelled),
                         "reverted_approvals": reverted})
        return result
    finally:
        session.close()
