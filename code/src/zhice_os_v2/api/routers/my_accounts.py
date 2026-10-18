"""owner-scoped 用户账户 API：/my/accounts。

普通业务用户(USER)经 user_owner_scope_only 中间件仅可写 /api/v1/my/*。此路由从 JWT 取
current_user 作为 owner 边界，请求体只含 market/account_type/initial_capital，
绝不接受 user_id（防越权归属）。列表仅返回本人账户。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from zhice_os_v2.api.dependencies import (
    get_current_user,
    get_user_account_service,
    require_roles,
)
from zhice_os_v2.application.services.user_account_service import UserAccountService
from zhice_os_v2.domain.accounts.user_account import AccountType, UserAccount
from zhice_os_v2.domain.market.registry import SUPPORTED_MARKETS
from zhice_os_v2.domain.security.user import Role, User

router = APIRouter(
    prefix="/my/accounts", tags=["my-accounts"], dependencies=[Depends(get_current_user)]
)

# owner-scoped 写面限业务用户(USER)与管理员(ADMIN)，与 my_strategies 一致。
_OWNER_WRITE_ROLES = (Role.USER, Role.ADMIN)


def _serialize(a: UserAccount) -> dict:
    return {
        "account_id": a.account_id,
        "user_id": a.user_id,
        "market": a.market,
        "currency": a.currency,
        "account_type": str(a.account_type),
        "initial_capital": str(a.initial_capital),
        "cash_balance": str(a.cash_balance),
        "reserved_cash": str(a.reserved_cash),
        "status": a.status,
    }


@router.get("")
def list_my_accounts(
    market: str | None = None,
    user: User = Depends(get_current_user),
    service: UserAccountService = Depends(get_user_account_service),
):
    """仅列出本人账户（可按市场过滤）。"""
    return [_serialize(a) for a in service.list_owned(user, market=market)]


class CreateAccountBody(BaseModel):
    market: str
    account_type: str
    initial_capital: float
    # 注意：绝不接受 user_id——owner 由 JWT 决定。


@router.post("")
def create_my_account(
    body: CreateAccountBody,
    user: User = Depends(require_roles(*_OWNER_WRITE_ROLES)),
    service: UserAccountService = Depends(get_user_account_service),
):
    # 入参层校验（deep-test B4）：service 层 AccountType()/currency_for_market 抛
    # ValueError 未捕获 → 500；此处统一 422 {code}（与 errors.json i18n 惯例一致）。
    if body.market not in SUPPORTED_MARKETS:
        raise HTTPException(status_code=422, detail={"code": "market.invalid"})
    try:
        AccountType(body.account_type)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "account.invalid_account_type"}
        ) from exc
    if body.initial_capital <= 0:
        raise HTTPException(status_code=422, detail={"code": "account.invalid_capital"})
    account = service.create(
        user,
        market=body.market,
        account_type=body.account_type,
        initial_capital=body.initial_capital,
    )
    return _serialize(account)
