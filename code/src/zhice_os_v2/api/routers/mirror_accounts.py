"""跟单模拟盘 API（/my/mirror-accounts，owner 隔离）。建账户需活跃会员+活跃订阅。"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException

from zhice_os_v2.api.dependencies import (
    get_current_user,
    get_mirror_sim_service,
    require_active_membership,
    require_roles,
)
from zhice_os_v2.api.schemas.mirror_accounts import CreateMirrorAccountBody
from zhice_os_v2.application.services.mirror_sim_service import MirrorSimError, MirrorSimService
from zhice_os_v2.domain.security.user import Role, User

router = APIRouter(prefix="/my/mirror-accounts", tags=["my-mirror-accounts"],
                   dependencies=[Depends(get_current_user)])
_OWNER_ROLES = (Role.USER, Role.ADMIN)
_STATUS = {
    "mirror.subscription_required": 403, "mirror.not_observable_strategy": 403,
    "mirror.invalid_capital": 422, "mirror.account_exists": 409, "mirror.account_not_found": 404,
}


def _raise(e: MirrorSimError):
    raise HTTPException(status_code=_STATUS.get(e.code, 400), detail={"code": e.code}) from e


@router.post("", dependencies=[Depends(require_active_membership)])
def create_mirror_account(body: CreateMirrorAccountBody,
                          user: User = Depends(require_roles(*_OWNER_ROLES)),
                          service: MirrorSimService = Depends(get_mirror_sim_service)):
    try:
        return service.create_account(user, strategy_id=body.strategy_id,
                                      initial_capital=Decimal(str(body.initial_capital)))
    except MirrorSimError as e:
        _raise(e)


@router.get("")
def list_mirror_accounts(user: User = Depends(get_current_user),
                         service: MirrorSimService = Depends(get_mirror_sim_service)):
    return service.list_accounts(user)


@router.get("/{strategy_id}")
def mirror_account_detail(strategy_id: str, user: User = Depends(get_current_user),
                          service: MirrorSimService = Depends(get_mirror_sim_service)):
    try:
        return service.detail(user, strategy_id=strategy_id)
    except MirrorSimError as e:
        _raise(e)
