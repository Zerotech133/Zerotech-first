"""管理员全局只读监督 API：/admin/*（仅 ADMIN）。

规格：管理员全局查看必须走**显式 admin API**（不复用 owner-scoped /my/*），且每次查看
在返回前写一条持久化审计 v2_access_audit（action=LIST_GLOBAL）。审计只落元数据，绝不落
Token/密码/响应正文。审计失败 fail-open（record_access 内部记 ERROR 日志、不阻断只读查看）。

这些路由是**非 owner-scoped 的全局列表**（list_all），与 owner-scoped 的 /my/* 严格区分。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from zhice_os_v2.api.dependencies import (
    get_strategy_catalog_service,
    get_user_account_service,
    get_user_strategy_service,
    record_access,
    require_roles,
)
from zhice_os_v2.application.services.strategy_catalog_service import (
    StrategyCatalogService,
)
from zhice_os_v2.application.services.user_account_service import UserAccountService
from zhice_os_v2.application.services.user_strategy_service import UserStrategyService
from zhice_os_v2.domain.accounts.user_account import UserAccount
from zhice_os_v2.domain.security.user import Role, User
from zhice_os_v2.domain.strategy.strategy import Strategy
from zhice_os_v2.domain.strategy.user_strategy import UserStrategyInstance

router = APIRouter(prefix="/admin", tags=["admin-oversight"])

_ACTION_LIST_GLOBAL = "LIST_GLOBAL"


def _serialize_instance(i: UserStrategyInstance) -> dict:
    return {
        "user_strategy_id": i.user_strategy_id,
        "user_id": i.user_id,
        "strategy_version_id": i.strategy_version_id,
        "account_id": i.account_id,
        "enabled": i.enabled,
        "auto_execute": i.auto_execute,
        "execution_mode": str(i.execution_mode),
        "capital_quota": (None if i.capital_quota is None else str(i.capital_quota)),
        "max_position_ratio": str(i.max_position_ratio),
    }


def _serialize_account(a: UserAccount) -> dict:
    return {
        "account_id": a.account_id,
        "user_id": a.user_id,
        "market": a.market,
        "currency": a.currency,
        "account_type": a.account_type.value,
        "initial_capital": str(a.initial_capital),
        "cash_balance": str(a.cash_balance),
        "reserved_cash": str(a.reserved_cash),
        "status": a.status,
    }


def _serialize_strategy(s: Strategy) -> dict:
    return {
        "strategy_id": s.strategy_id,
        "strategy_name": s.strategy_name,
        "market": s.market,
        "status": str(s.status),
        "owner_user_id": s.owner_user_id,
        "visibility": s.visibility,
        "publication_status": s.publication_status,
    }


@router.get("/strategy-instances")
def list_all_strategy_instances(
    user: User = Depends(require_roles(Role.ADMIN)),
    service: UserStrategyService = Depends(get_user_strategy_service),
):
    """列全部用户运行实例（全局，非 owner-scoped）。返回前写审计。"""
    instances = service.list_all()
    record_access(user, _ACTION_LIST_GLOBAL, "user_strategy_instance")
    return [_serialize_instance(i) for i in instances]


@router.get("/accounts")
def list_all_accounts(
    market: str | None = None,
    user: User = Depends(require_roles(Role.ADMIN)),
    service: UserAccountService = Depends(get_user_account_service),
):
    """列全部用户账户（全局，非 owner-scoped）。返回前写审计。"""
    accounts = service.list_all()
    if market is not None:
        accounts = [a for a in accounts if a.market == market]
    record_access(user, _ACTION_LIST_GLOBAL, "user_account")
    return [_serialize_account(a) for a in accounts]


@router.get("/strategies")
def list_all_strategies(
    market: str | None = None,
    user: User = Depends(require_roles(Role.ADMIN)),
    catalog: StrategyCatalogService = Depends(get_strategy_catalog_service),
):
    """列全部策略（含所有用户私有；全局）。返回前写审计。"""
    strategies = catalog.list_all(market=market)
    record_access(user, _ACTION_LIST_GLOBAL, "strategy")
    return [_serialize_strategy(s) for s in strategies]
