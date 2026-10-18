"""owner-scoped 交易读 API：/my/plans、/my/positions、/my/replays、/my/approvals。

铁律（Phase B Task 6）：
- owner 恒取自 JWT current_user，**绝不**接受请求体/查询里的 user_id。
- 单资源读：非 owner / 不存在 / legacy（NULL 归属）一律 404（not-found 等价，不泄露存在性），
  经 require_owned + load_owned_* 统一处理。
- 列表读：只返回本人（user_strategy_id 属本人实例）的资源；legacy NULL 与他人资源不返回。
- 纯新增只读路由，不改 legacy 执行/现有全局路由（plans.py/positions.py/replay.py/approval.py）行为。
  管理员全局查看走独立的 /admin/*（admin_oversight.py，带 record_access 审计）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from zhice_os_v2.api.dependencies import (
    get_current_user,
    get_db,
    get_user_strategy_service,
)
from zhice_os_v2.api.dependencies_ownership import (
    load_owned_approval,
    load_owned_plan,
    load_owned_position,
    load_owned_replay,
    require_owned,
)
from zhice_os_v2.application.services.user_strategy_service import UserStrategyService
from zhice_os_v2.domain.security.user import User
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

router = APIRouter(prefix="/my", tags=["my-trading"], dependencies=[Depends(get_current_user)])


# ---------- 序列化（含归属字段，便于前端/测试断言 owner）----------
def _plan(p: Any) -> dict:
    return {
        "plan_id": p.plan_id,
        "strategy_version_id": p.strategy_version_id,
        "market": p.market,
        "ts_code": p.ts_code,
        "direction": p.direction,
        "ref_price": str(p.ref_price) if p.ref_price is not None else None,
        "stop_loss": str(p.stop_loss) if p.stop_loss is not None else None,
        "take_profit": str(p.take_profit) if p.take_profit is not None else None,
        "status": str(p.status.value) if hasattr(p.status, "value") else str(p.status),
        "user_strategy_id": p.user_strategy_id,
        "account_id": p.account_id,
    }


def _position(p: Any) -> dict:
    return {
        "position_id": p.position_id,
        "strategy_version_id": p.strategy_version_id,
        "market": p.market,
        "ts_code": p.ts_code,
        "direction": p.direction,
        "open_qty": str(p.open_qty),
        "open_price": str(p.open_price),
        "status": p.status.value if hasattr(p.status, "value") else str(p.status),
        "user_strategy_id": p.user_strategy_id,
        "account_id": p.account_id,
    }


def _replay(r: Any) -> dict:
    return {
        "replay_id": r.replay_id,
        "strategy_version_id": r.strategy_version_id,
        "market": r.market,
        "ts_code": r.ts_code,
        "pnl": str(r.pnl),
        "pnl_pct": str(r.pnl_pct),
        "exit_reason": r.exit_reason,
        "status": r.status.value if hasattr(r.status, "value") else str(r.status),
        "user_strategy_id": r.user_strategy_id,
        "account_id": r.account_id,
    }


def _approval(a: Any) -> dict:
    return {
        "request_id": a.request_id,
        "order_intent_id": a.order_intent_id,
        "strategy_version_id": a.strategy_version_id,
        "market": a.market,
        "ts_code": a.ts_code,
        "direction": a.direction,
        "qty": str(a.qty),
        "price": str(a.price) if a.price is not None else None,
        "status": a.status.value if hasattr(a.status, "value") else str(a.status),
        "user_strategy_id": a.user_strategy_id,
        "account_id": a.account_id,
    }


def _owned_ids(user: User, service: UserStrategyService) -> set[str]:
    return service.list_owned_instance_ids(user.user_id)


# ---------- 单资源读（owner-scoped，非 owner→404）----------
@router.get("/plans/{resource_id}")
def get_my_plan(resource=Depends(require_owned(load_owned_plan))):
    return _plan(resource)


@router.get("/positions/{resource_id}")
def get_my_position(resource=Depends(require_owned(load_owned_position))):
    return _position(resource)


@router.get("/replays/{resource_id}")
def get_my_replay(resource=Depends(require_owned(load_owned_replay))):
    return _replay(resource)


@router.get("/approvals/{resource_id}")
def get_my_approval(resource=Depends(require_owned(load_owned_approval))):
    return _approval(resource)


# ---------- 列表读（只返回本人资源；legacy NULL/他人不返回）----------
@router.get("/plans")
def list_my_plans(
    user: User = Depends(get_current_user),
    db: PersistencePort = Depends(get_db),
    service: UserStrategyService = Depends(get_user_strategy_service),
):
    owned = _owned_ids(user, service)
    return [
        _plan(p)
        for p in db.list_trade_plans()
        if p.user_strategy_id is not None and p.user_strategy_id in owned
    ]


@router.get("/positions")
def list_my_positions(
    user: User = Depends(get_current_user),
    db: PersistencePort = Depends(get_db),
    service: UserStrategyService = Depends(get_user_strategy_service),
):
    owned = _owned_ids(user, service)
    return [
        _position(p)
        for p in db.list_positions()
        if p.user_strategy_id is not None and p.user_strategy_id in owned
    ]


@router.get("/replays")
def list_my_replays(
    user: User = Depends(get_current_user),
    db: PersistencePort = Depends(get_db),
    service: UserStrategyService = Depends(get_user_strategy_service),
):
    owned = _owned_ids(user, service)
    return [
        _replay(r)
        for r in db.list_replays()
        if r.user_strategy_id is not None and r.user_strategy_id in owned
    ]


@router.get("/approvals")
def list_my_approvals(
    user: User = Depends(get_current_user),
    db: PersistencePort = Depends(get_db),
    service: UserStrategyService = Depends(get_user_strategy_service),
):
    owned = _owned_ids(user, service)
    return [
        _approval(a)
        for a in db.list_approvals()
        if a.user_strategy_id is not None and a.user_strategy_id in owned
    ]
