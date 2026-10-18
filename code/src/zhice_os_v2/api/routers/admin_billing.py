"""计费/权益 管理侧 API（P1，ADMIN）：/admin/billing。

- GET  /admin/billing/pricing            读定价
- PUT  /admin/billing/pricing            改定价（会员月费 / 策略默认价 / 按策略覆盖）
- POST /admin/billing/grant-membership   手动发放/续费会员 N 月
- POST /admin/billing/grant-subscription 手动发放/续订某策略订阅 N 月

手动发放端点是 P3 支付闭环上线前的联调 & 客服补单通道；上线后由回调自动发放，
本端点保留供人工补偿。改价模式对齐 settings.py（持久化到 v2_app_state）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from zhice_os_v2.api.dependencies import require_roles
from zhice_os_v2.application.services.membership_service import (
    get_pricing,
    set_pricing,
)
from zhice_os_v2.domain.security.user import Role, User

router = APIRouter(prefix="/admin/billing", tags=["admin-billing"])

# B1-9：grant 端点错误码 → HTTP（与 admin_entitlements 一致）。
_GRANT_ERROR_STATUS = {
    "entitlements.user_not_found": 404,
    "entitlements.strategy_not_grantable": 400,
    "entitlements.version_conflict": 409,
    "entitlements.idempotency_conflict": 409,
    "entitlements.invalid_batch": 422,
    "entitlements.invalid_idempotency_key": 422,
    "entitlements.invalid_reason": 422,
}


class PricingBody(BaseModel):
    membership_monthly_usdt: float | None = Field(default=None, gt=0)
    strategy_default_monthly_usdt: float | None = Field(default=None, gt=0)
    # {strategy_id: monthly_usdt}
    strategy_overrides: dict[str, float] | None = None


class GrantMembershipBody(BaseModel):
    user_id: str
    months: int = Field(ge=1, le=120)
    # B1-9：审计原因 + 乐观锁版本（缺行为 0）。
    reason: str
    version: int = Field(ge=0)


class GrantSubscriptionBody(BaseModel):
    user_id: str
    strategy_id: str
    months: int = Field(ge=1, le=120)
    reason: str
    version: int = Field(ge=0)


@router.get("/pricing")
def read_pricing(_: User = Depends(require_roles(Role.ADMIN))):
    return get_pricing()


@router.put("/pricing")
def update_pricing(body: PricingBody, _: User = Depends(require_roles(Role.ADMIN))):
    patch = body.model_dump(exclude_none=True)
    return set_pricing(patch)


def _grant_context(actor_user_id: str, idempotency_key: str | None, *,
                   route: str, action: str, target_user_id: str, body: dict):
    """构造审计写上下文（规范化 Idempotency-Key + 请求指纹）；非法键 422。"""
    from zhice_os_v2.application.services.admin_operation_fingerprint import (
        IdempotencyKeyError,
        normalize_idempotency_key,
        request_fingerprint,
    )
    from zhice_os_v2.domain.billing.admin_entitlement import AdminWriteContext

    try:
        key = normalize_idempotency_key(idempotency_key)
    except IdempotencyKeyError as e:
        raise HTTPException(status_code=422, detail={"code": e.code}) from e
    fp = request_fingerprint(
        method="POST", route=route, action=action,
        target_user_id=target_user_id, body=body,
    )
    return AdminWriteContext(
        actor_user_id=actor_user_id, idempotency_key=key, request_fingerprint=fp
    )


def _grant_service():
    from zhice_os_v2.application.services.admin_entitlement_service import (
        AdminEntitlementService,
    )
    return AdminEntitlementService()


def _clean_reason(reason: str) -> str:
    r = reason.strip()
    if not r or len(r) > 500:
        raise HTTPException(
            status_code=422, detail={"code": "entitlements.invalid_reason"}
        )
    return r


@router.post("/grant-membership")
def grant_membership(
    body: GrantMembershipBody,
    actor: User = Depends(require_roles(Role.ADMIN)),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """客服补单：会员续费（B1-9 经审计事务 + 乐观锁 + 幂等；不接受 order_id）。"""
    from zhice_os_v2.application.services.admin_entitlement_service import (
        EntitlementError,
    )

    reason = _clean_reason(body.reason)
    ctx = _grant_context(
        actor.user_id, idempotency_key,
        route="/api/v1/admin/billing/grant-membership", action="membership.grant",
        target_user_id=body.user_id,
        body={"months": body.months, "reason": body.reason, "version": body.version},
    )
    try:
        result = _grant_service().grant_membership_months(
            context=ctx, target_user_id=body.user_id, months=body.months,
            reason=reason, expected_version=body.version,
        )
    except EntitlementError as e:
        raise HTTPException(
            status_code=_GRANT_ERROR_STATUS.get(e.code, 400), detail={"code": e.code}
        ) from e
    return result.body


@router.post("/grant-subscription")
def grant_subscription(
    body: GrantSubscriptionBody,
    actor: User = Depends(require_roles(Role.ADMIN)),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """客服补单：策略订阅续订（B1-9 经审计事务 + 资格谓词 + 乐观锁 + 幂等）。"""
    from zhice_os_v2.application.services.admin_entitlement_service import (
        EntitlementError,
    )

    reason = _clean_reason(body.reason)
    ctx = _grant_context(
        actor.user_id, idempotency_key,
        route="/api/v1/admin/billing/grant-subscription",
        action="subscription.grant", target_user_id=body.user_id,
        body={"strategy_id": body.strategy_id, "months": body.months,
              "reason": body.reason, "version": body.version},
    )
    try:
        result = _grant_service().grant_subscription_months(
            context=ctx, target_user_id=body.user_id, strategy_id=body.strategy_id,
            months=body.months, reason=reason, expected_version=body.version,
        )
    except EntitlementError as e:
        raise HTTPException(
            status_code=_GRANT_ERROR_STATUS.get(e.code, 400), detail={"code": e.code}
        ) from e
    return result.body
