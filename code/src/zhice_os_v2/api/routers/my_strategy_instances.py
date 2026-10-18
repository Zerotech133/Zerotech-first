"""owner-scoped 用户策略运行实例 API：/my/strategy-instances。

普通业务用户(USER)经 user_owner_scope_only 中间件仅可写 /api/v1/my/*。此路由从 JWT 取
current_user 作为 owner 边界，请求体绝不接受 user_id（owner 恒取自 JWT）。列表/启停/更新
仅作用于本人实例；非 owner 一律 404（not-found 等价，不泄露存在性）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from zhice_os_v2.api.dependencies import (
    get_current_user,
    get_user_strategy_service,
    require_active_membership,
    require_roles,
)
from zhice_os_v2.application.services.user_strategy_service import UserStrategyService
from zhice_os_v2.domain.security.user import Role, User
from zhice_os_v2.domain.strategy.user_strategy import UserStrategyInstance

router = APIRouter(
    prefix="/my/strategy-instances",
    tags=["my-strategy-instances"],
    dependencies=[Depends(get_current_user)],
)

# owner-scoped 写面限业务用户(USER)与管理员(ADMIN)，与 my_accounts/my_strategies 一致。
_OWNER_WRITE_ROLES = (Role.USER, Role.ADMIN)


def _serialize(i: UserStrategyInstance) -> dict:
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
        "risk_parameters": i.risk_parameters,
        "strategy_parameters": i.strategy_parameters,
        "notification_preferences": i.notification_preferences,
        "upgrade_available_version_id": i.upgrade_available_version_id,
        "pending_upgrade_version_id": i.pending_upgrade_version_id,
        "upgrade_requested_at": (
            None if i.upgrade_requested_at is None else i.upgrade_requested_at.isoformat()
        ),
    }


@router.get("")
def list_my_instances(
    market: str | None = None,
    enabled: bool | None = None,
    user: User = Depends(get_current_user),
    service: UserStrategyService = Depends(get_user_strategy_service),
):
    """仅列出本人运行实例（可按市场/启用状态过滤）。"""
    return [
        _serialize(i)
        for i in service.list_owned(user, market=market, enabled=enabled)
    ]


class InstanceCreateBody(BaseModel):
    # account_mode 缺省为 None：不传 account_id 时按 NEW_DEDICATED 建独立子账户；
    # 传 account_id 时绑既有账户。显式 NEW_DEDICATED + account_id 属矛盾（服务层 400）。
    strategy_version_id: str
    account_mode: str | None = None
    account_id: str | None = None
    initial_capital: float | None = None
    execution_mode: str = "MANUAL"
    auto_execute: bool = False
    capital_quota: float | None = None
    max_position_ratio: float = 0.1
    risk_parameters: dict | None = None
    strategy_parameters: dict | None = None
    notification_preferences: dict | None = None
    # 注意：绝不接受 user_id——owner 由 JWT 决定。


@router.post("", dependencies=[Depends(require_active_membership)])
def create_my_instance(
    body: InstanceCreateBody,
    user: User = Depends(require_roles(*_OWNER_WRITE_ROLES)),
    service: UserStrategyService = Depends(get_user_strategy_service),
):
    try:
        inst = service.create(
            user,
            strategy_version_id=body.strategy_version_id,
            account_mode=body.account_mode,
            account_id=body.account_id,
            initial_capital=body.initial_capital,
            execution_mode=body.execution_mode,
            auto_execute=body.auto_execute,
            capital_quota=body.capital_quota,
            max_position_ratio=body.max_position_ratio,
            risk_parameters=body.risk_parameters,
            strategy_parameters=body.strategy_parameters,
            notification_preferences=body.notification_preferences,
        )
    except LookupError as exc:
        # 不泄露他人策略/账户存在性：统一 404。
        raise HTTPException(status_code=404, detail="not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize(inst)


class InstanceUpdateBody(BaseModel):
    auto_execute: bool | None = None
    execution_mode: str | None = None
    capital_quota: float | None = None
    max_position_ratio: float | None = None
    risk_parameters: dict | None = None
    strategy_parameters: dict | None = None
    notification_preferences: dict | None = None


@router.patch("/{user_strategy_id}", dependencies=[Depends(require_active_membership)])
def update_my_instance(
    user_strategy_id: str,
    body: InstanceUpdateBody,
    user: User = Depends(require_roles(*_OWNER_WRITE_ROLES)),
    service: UserStrategyService = Depends(get_user_strategy_service),
):
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        inst = service.update_owned(user, user_strategy_id, patch)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize(inst)


class UpgradeBody(BaseModel):
    target_version_id: str


@router.patch(
    "/{user_strategy_id}/upgrade", dependencies=[Depends(require_active_membership)]
)
def upgrade_my_instance(
    user_strategy_id: str,
    body: UpgradeBody,
    user: User = Depends(require_roles(*_OWNER_WRITE_ROLES)),
    service: UserStrategyService = Depends(get_user_strategy_service),
):
    """记录升级意图（owner-scoped；仅置 pending 字段不切换——Phase A 服务语义）。"""
    try:
        inst = service.request_upgrade(user, user_strategy_id, body.target_version_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize(inst)


@router.patch(
    "/{user_strategy_id}/enable", dependencies=[Depends(require_active_membership)]
)
def enable_my_instance(
    user_strategy_id: str,
    user: User = Depends(require_roles(*_OWNER_WRITE_ROLES)),
    service: UserStrategyService = Depends(get_user_strategy_service),
):
    try:
        inst = service.enable(user, user_strategy_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _serialize(inst)


@router.patch("/{user_strategy_id}/disable")
def disable_my_instance(
    user_strategy_id: str,
    user: User = Depends(require_roles(*_OWNER_WRITE_ROLES)),
    service: UserStrategyService = Depends(get_user_strategy_service),
):
    try:
        inst = service.disable(user, user_strategy_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    return _serialize(inst)


@router.delete("/{user_strategy_id}")
def delete_my_instance(
    user_strategy_id: str,
    user: User = Depends(require_roles(*_OWNER_WRITE_ROLES)),
    service: UserStrategyService = Depends(get_user_strategy_service),
):
    """删除本人运行实例（B5 运营死锁解锁）。

    仅当实例已 disabled 且无 OPEN 持仓时允许（否则 409 {code}：
    instance.not_disabled / instance.has_open_positions，errors.json 中英键）。
    删除连带释放该实例资金预留；专用账户且无其他实例引用时连账户删。
    """
    try:
        result = service.delete_owned(user, user_strategy_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409, detail={"code": str(exc)}
        ) from exc
    return {
        "deleted": user_strategy_id,
        "account_id": result.get("account_id"),
    }
