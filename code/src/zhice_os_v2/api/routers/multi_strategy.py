"""多策略治理 API（V1 multi-strategy 对齐）。

V1 参照 zhice/backend multi-strategy + MultiStrategyPage.tsx：
  - 冲突检测：同日同 ts_code 被多个策略生成 PENDING/APPROVED 计划 → 冲突组
  - 裁决（写库 plan_status='CANCELLED'）：
      PRIORITY    按 priority_order（缺省按 confidence_level 降序）保留第一名，其余取消
      BLOCK_ALL   冲突组全部取消
      MERGE_DEDUP 合并去重语义：保留信心分最高者，其余取消
  - 配额治理：每策略 quota_pct（资金配额 %）/ max_positions（最大持仓数），
    内存态配置（重启恢复默认，未配置时总览显示 null 且不判超限）
"""
from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator
from sqlalchemy import func

from zhice_os_v2.api.dependencies import get_current_user, get_db
from zhice_os_v2.api.routers.app_state_store import _load_all, _save_state
from zhice_os_v2.application.services.account_stats_service import AccountStatsService
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

router = APIRouter(prefix="/multi-strategy", tags=["multi-strategy"], dependencies=[Depends(get_current_user)])

_QUOTA_PREFIX = "quota:"


def _quota_key(sv_id: str) -> str:
    return f"{_QUOTA_PREFIX}{sv_id}"


def _load_quotas() -> dict[str, dict]:
    """从 v2_app_state 读全部配额（quota:<sv_id> -> {quota_pct, max_positions}）。

    返回 {sv_id: quota}；DB 失败时回退内存层（app_state_store 内建）。
    """
    rows = _load_all(_QUOTA_PREFIX)
    return {k[len(_QUOTA_PREFIX):]: v for k, v in rows.items()}


# 启动时从 DB 加载（重启保留配额配置）
_quotas: dict[str, dict] = _load_quotas()

# 参与冲突检测/裁决的计划状态（尚未落地执行的活跃计划）
ACTIVE_PLAN_STATUSES = ("PENDING", "APPROVED")
RESOLVE_MODES = ("PRIORITY", "BLOCK_ALL", "MERGE_DEDUP")


class ResolveBody(BaseModel):
    market: str = "CN"
    date: str | None = None
    mode: str
    priority_order: list[str] | None = None  # sv_id 优先顺序（PRIORITY 模式用）

    @field_validator("mode")
    @classmethod
    def _mode_valid(cls, v: str) -> str:
        if v not in RESOLVE_MODES:
            raise ValueError(f"mode must be one of {RESOLVE_MODES}")
        return v


class QuotaBody(BaseModel):
    quota_pct: float  # 0-100
    max_positions: int  # > 0

    @field_validator("quota_pct")
    @classmethod
    def _quota_range(cls, v: float) -> float:
        if not 0 <= v <= 100:
            raise ValueError("quota_pct must be within 0-100")
        return v

    @field_validator("max_positions")
    @classmethod
    def _positions_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("max_positions must be > 0")
        return v


def _load_active_plans(market: str, day: str | None):
    """指定日（缺省取最新有活跃计划的日期）的 PENDING/APPROVED 计划。返回 (date, rows)。"""
    session = get_session()
    try:
        base = session.query(orm.TradePlanModel).filter(
            orm.TradePlanModel.market == market,
            orm.TradePlanModel.plan_status.in_(ACTIVE_PLAN_STATUSES),
        )
        if day is None:
            day = (
                session.query(func.max(func.date(orm.TradePlanModel.created_at)))
                .filter(
                    orm.TradePlanModel.market == market,
                    orm.TradePlanModel.plan_status.in_(ACTIVE_PLAN_STATUSES),
                )
                .scalar()
            )
        if day is None:
            return None, []
        rows = (
            base.filter(func.date(orm.TradePlanModel.created_at) == day)
            .order_by(orm.TradePlanModel.ts_code, orm.TradePlanModel.created_at)
            .all()
        )
        return str(day), rows
    finally:
        session.close()


def _group_conflicts(rows) -> tuple[list[tuple[str, list]], int]:
    """按 ts_code 分组：多于一个策略版本的为冲突组；返回 (冲突组, 无冲突计划数)。"""
    groups: dict[str, list] = defaultdict(list)
    for p in rows:
        groups[p.ts_code].append(p)
    conflicts: list[tuple[str, list]] = []
    clean_count = 0
    for ts_code, plans in groups.items():
        if len({p.strategy_version_id for p in plans}) > 1:
            conflicts.append((ts_code, plans))
        else:
            clean_count += len(plans)
    return sorted(conflicts, key=lambda g: g[0]), clean_count


@router.get("/conflicts")
def conflicts(market: str = "CN", date: str | None = None):
    """检测冲突：指定日（默认最新计划日）同 ts_code 多策略 PENDING/APPROVED 计划分组。"""
    day, rows = _load_active_plans(market, date)
    names = AccountStatsService._strategy_names()
    conflict_groups, clean_count = _group_conflicts(rows)
    return {
        "date": day,
        "market": market,
        "conflicts": [
            {
                "ts_code": ts_code,
                "plans": sorted(
                    (
                        {
                            "plan_id": p.plan_id,
                            "strategy_name": names.get(p.strategy_version_id, p.strategy_version_id),
                            "strategy_version_id": p.strategy_version_id,
                            "plan_status": p.plan_status,
                            "confidence_level": (
                                float(p.confidence_level) if p.confidence_level is not None else None
                            ),
                        }
                        for p in plans
                    ),
                    key=lambda x: -(x["confidence_level"] or 0.0),
                ),
            }
            for ts_code, plans in conflict_groups
        ],
        "clean_count": clean_count,
    }


@router.post("/resolve")
def resolve(body: ResolveBody):
    """执行裁决（写库 v2_trade_plan.plan_status='CANCELLED'）。"""
    day, rows = _load_active_plans(body.market, body.date)
    conflict_groups, _ = _group_conflicts(rows)
    if not conflict_groups:
        return {
            "date": day,
            "mode": body.mode,
            "resolved_groups": 0,
            "cancelled_plans": [],
        }

    order = body.priority_order or []

    def _priority_rank(p):
        # priority_order 靠前者胜；未列入者排最后；同位次按信心分降序
        try:
            pos = order.index(p.strategy_version_id)
        except ValueError:
            pos = len(order)
        return (pos, -float(p.confidence_level or 0.0))

    cancelled: list[str] = []
    session = get_session()
    try:
        for _ts_code, plans in conflict_groups:
            if body.mode == "BLOCK_ALL":
                losers = plans
            elif body.mode == "MERGE_DEDUP":
                # 合并去重语义：保留信心分最高者（与 PRIORITY 的差别：不看 priority_order）
                losers = sorted(plans, key=lambda p: -float(p.confidence_level or 0.0))[1:]
            else:  # PRIORITY
                losers = sorted(plans, key=_priority_rank)[1:]
            for p in losers:
                session.query(orm.TradePlanModel).filter(
                    orm.TradePlanModel.plan_id == p.plan_id
                ).update({"plan_status": "CANCELLED"}, synchronize_session=False)
                cancelled.append(p.plan_id)
        session.commit()
    finally:
        session.close()
    return {
        "date": day,
        "mode": body.mode,
        "resolved_groups": len(conflict_groups),
        "cancelled_plans": cancelled,
    }


@router.get("/quotas")
def get_quotas():
    return _load_quotas()


@router.put("/quotas/{sv_id}")
def set_quota(sv_id: str, body: QuotaBody):
    quota = {"quota_pct": body.quota_pct, "max_positions": body.max_positions}
    _save_state(_quota_key(sv_id), quota)
    _quotas[sv_id] = quota
    return _quotas[sv_id]


@router.get("/overview")
def governance_overview(market: str = "CN", db: PersistencePort = Depends(get_db)):
    """多策略治理总览：每策略持仓/市值/仓位 + 配额超限标记（position 数据复用 AccountStatsService）。

    配额每次从 DB 读（多进程/重启后配置一致）。
    """
    accounts = AccountStatsService(db).by_strategy(market)
    quotas = _load_quotas()
    out = []
    for a in accounts:
        sv_id = a["strategy_version_id"]
        quota = quotas.get(sv_id)
        quota_pct = quota["quota_pct"] if quota else None
        max_positions = quota["max_positions"] if quota else None
        out.append(
            {
                "strategy_version_id": sv_id,
                "strategy_name": a["strategy_name"],
                "position_count": a["position_count"],
                "market_value": a["market_value"],
                "position_ratio": a["position_ratio"],
                "quota_pct": quota_pct,
                "max_positions": max_positions,
                "over_exposure": quota_pct is not None and a["position_ratio"] > quota_pct,
                "over_positions": max_positions is not None and a["position_count"] > max_positions,
            }
        )
    return out
