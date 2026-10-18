"""管理员权益管理 API（B 单元，ADMIN）：/admin/entitlements。

B1-7 只读：
- GET  /admin/entitlements/users            用户摘要分页（会员实时状态 + 有效订阅数）
- GET  /admin/entitlements/users/{user_id}  单用户权益详情 + 最近审计 + 支付摘要
- GET  /admin/entitlements/audits           审计事件分页（时间过滤须带时区）

B1-8 写入（幂等 + 乐观锁；错误码 → HTTP；JSONResponse 精确复放）：
- PUT  /admin/entitlements/users/{user_id}/membership          设置/续费会员
- PUT  /admin/entitlements/users/{user_id}/subscriptions       批量设置/续订
- POST /admin/entitlements/users/{user_id}/subscriptions/revoke 批量撤销订阅

B2-4 撤销会员（撤销交易边界已就绪后注册）：
- POST /admin/entitlements/users/{user_id}/membership/revoke  撤会员 + 停新增开仓风险
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from zhice_os_v2.api.dependencies import require_roles
from zhice_os_v2.api.schemas.admin_entitlements import (
    AuditQueryParams,
    PaymentGrantsQueryParams,
    RetryGrantBody,
    ReviewGrantBody,
    RevokeMembershipBody,
    RevokeSubscriptionsBody,
    SetMembershipBody,
    SetSubscriptionsBody,
)
from zhice_os_v2.application.services.admin_entitlement_service import (
    AdminEntitlementService,
    EntitlementError,
)
from zhice_os_v2.application.services.admin_operation_fingerprint import (
    IdempotencyKeyError,
    normalize_idempotency_key,
    request_fingerprint,
)
from zhice_os_v2.application.services.payment_grant_service import (
    GrantAdminError,
    PaymentGrantService,
)
from zhice_os_v2.domain.billing.admin_entitlement import (
    AdminWriteContext,
    AdminWriteResult,
    AuditQuery,
)
from zhice_os_v2.domain.security.user import Role, User

router = APIRouter(prefix="/admin/entitlements", tags=["admin-entitlements"])

_AUDIT_MAX_PAGE_SIZE = 200

# 错误码 → HTTP 状态。未列出的 entitlements.* 默认 400。
_ERROR_STATUS = {
    "entitlements.user_not_found": 404,
    "entitlements.strategy_not_grantable": 400,
    "entitlements.version_conflict": 409,
    "entitlements.idempotency_conflict": 409,
    "entitlements.user_inactive": 409,
    "entitlements.subscription_not_granted": 409,
    "entitlements.membership_not_granted": 409,
    "entitlements.invalid_reason": 422,
    "entitlements.invalid_idempotency_key": 422,
    "entitlements.invalid_expiry": 422,
    "entitlements.invalid_batch": 422,
    "entitlements.versions_mismatch": 422,
    # B2-4：撤权释放预留时账实不符（reserved_cash 将变负）→ 冲突，整体回滚。
    "entitlements.reservation_balance_inconsistent": 409,
}


def _service() -> AdminEntitlementService:
    return AdminEntitlementService()


def _grant_service() -> PaymentGrantService:
    return PaymentGrantService()


_PAYMENT_GRANTS_MAX_PAGE_SIZE = 200

# C-5 支付发放操作错误码 → HTTP。未列出默认 400。
_GRANT_ERROR_STATUS = {
    "billing.order_not_found": 404,
    "billing.grant_review_required": 409,
    "billing.grant_in_progress": 409,
    "billing.not_reviewable": 409,
}


def _run_grant(fn):
    """执行 C-5 支付操作并把 GrantAdminError 映射为 HTTP 错误。"""
    try:
        return fn()
    except GrantAdminError as e:
        status = _GRANT_ERROR_STATUS.get(e.code, 400)
        raise HTTPException(status_code=status, detail={"code": e.code}) from e


def get_idempotency_key(
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> str:
    """读取并规范化 Idempotency-Key header；非法（缺失/长度/字符）→ 422。"""
    try:
        return normalize_idempotency_key(idempotency_key)
    except IdempotencyKeyError as e:
        raise HTTPException(
            status_code=422, detail={"code": e.code}
        ) from e


def _clean_reason(reason: str) -> str:
    """reason trim 后非空且 ≤500 字符，否则 422 invalid_reason。"""
    r = reason.strip()
    if not r or len(r) > 500:
        raise HTTPException(
            status_code=422, detail={"code": "entitlements.invalid_reason"}
        )
    return r


def _context(actor_user_id: str, key: str, fp: str) -> AdminWriteContext:
    return AdminWriteContext(
        actor_user_id=actor_user_id, idempotency_key=key, request_fingerprint=fp
    )


def _respond(result: AdminWriteResult) -> JSONResponse:
    """精确复放：直接回服务记录的状态码与响应体（幂等命中返回首次结果）。"""
    return JSONResponse(status_code=result.status_code, content=result.body)


def _run_write(fn) -> JSONResponse:
    """执行写操作并把 EntitlementError 映射为 HTTP 错误。"""
    try:
        return _respond(fn())
    except EntitlementError as e:
        status = _ERROR_STATUS.get(e.code, 400)
        raise HTTPException(status_code=status, detail={"code": e.code}) from e


@router.get("/users")
def list_users(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1),
    query: str | None = Query(default=None),
    _: User = Depends(require_roles(Role.ADMIN)),
):
    return _service().list_users(page=page, page_size=page_size, query=query)


@router.get("/users/{user_id}")
def get_user_detail(
    user_id: str,
    _: User = Depends(require_roles(Role.ADMIN)),
):
    try:
        return _service().get_user_detail(user_id)
    except EntitlementError as e:
        raise HTTPException(status_code=404, detail={"code": e.code}) from e


@router.get("/audits")
def list_audits(
    params: Annotated[AuditQueryParams, Query()],
    _: User = Depends(require_roles(Role.ADMIN)),
):
    page_size = min(_AUDIT_MAX_PAGE_SIZE, params.page_size)
    query = AuditQuery(
        page=params.page,
        page_size=page_size,
        target_user_id=params.target_user_id,
        actor_user_id=params.actor_user_id,
        action=params.action,
        resource_type=params.resource_type,
        strategy_id=params.strategy_id,
        from_at=params.from_at,
        to_at=params.to_at,
    )
    return _service().list_audits(query)


def _require_valid_expiry(mode: str, valid_until) -> None:
    """在算指纹前先做过期结构校验：mode=until 须带时区日期；permanent 不带。"""
    if mode == "until" and (valid_until is None or valid_until.tzinfo is None):
        raise HTTPException(
            status_code=422, detail={"code": "entitlements.invalid_expiry"}
        )
    if mode == "permanent" and valid_until is not None:
        raise HTTPException(
            status_code=422, detail={"code": "entitlements.invalid_expiry"}
        )


@router.put("/users/{user_id}/membership")
def set_membership(
    user_id: str,
    body: SetMembershipBody,
    idempotency_key: str = Depends(get_idempotency_key),
    actor: User = Depends(require_roles(Role.ADMIN)),
):
    reason = _clean_reason(body.reason)
    _require_valid_expiry(body.mode, body.valid_until)
    fp = request_fingerprint(
        method="PUT",
        route="/admin/entitlements/users/{user_id}/membership",
        action="membership.set",
        target_user_id=user_id,
        body={
            "mode": body.mode,
            "valid_until": body.valid_until.isoformat() if body.valid_until else None,
            "reason": body.reason,
            "version": body.version,
        },
    )
    ctx = _context(actor.user_id, idempotency_key, fp)
    return _run_write(lambda: _service().set_membership(
        context=ctx, target_user_id=user_id, mode=body.mode,
        valid_until=body.valid_until, reason=reason, expected_version=body.version,
    ))


@router.put("/users/{user_id}/subscriptions")
def set_subscriptions(
    user_id: str,
    body: SetSubscriptionsBody,
    idempotency_key: str = Depends(get_idempotency_key),
    actor: User = Depends(require_roles(Role.ADMIN)),
):
    reason = _clean_reason(body.reason)
    _require_valid_expiry(body.mode, body.valid_until)
    fp = request_fingerprint(
        method="PUT",
        route="/admin/entitlements/users/{user_id}/subscriptions",
        action="subscriptions.set",
        target_user_id=user_id,
        body={
            "strategy_ids": list(body.strategy_ids),
            "mode": body.mode,
            "valid_until": body.valid_until.isoformat() if body.valid_until else None,
            "reason": body.reason,
            "versions": dict(body.versions),
        },
    )
    ctx = _context(actor.user_id, idempotency_key, fp)
    return _run_write(lambda: _service().set_subscriptions(
        context=ctx, target_user_id=user_id, strategy_ids=body.strategy_ids,
        mode=body.mode, valid_until=body.valid_until, reason=reason,
        expected_versions=body.versions,
    ))


@router.post("/users/{user_id}/subscriptions/revoke")
def revoke_subscriptions(
    user_id: str,
    body: RevokeSubscriptionsBody,
    idempotency_key: str = Depends(get_idempotency_key),
    actor: User = Depends(require_roles(Role.ADMIN)),
):
    reason = _clean_reason(body.reason)
    fp = request_fingerprint(
        method="POST",
        route="/admin/entitlements/users/{user_id}/subscriptions/revoke",
        action="subscriptions.revoke",
        target_user_id=user_id,
        body={
            "strategy_ids": list(body.strategy_ids),
            "reason": body.reason,
            "versions": dict(body.versions),
        },
    )
    ctx = _context(actor.user_id, idempotency_key, fp)
    return _run_write(lambda: _service().revoke_subscriptions(
        context=ctx, target_user_id=user_id, strategy_ids=body.strategy_ids,
        reason=reason, expected_versions=body.versions,
    ))


@router.post("/users/{user_id}/membership/revoke")
def revoke_membership(
    user_id: str,
    body: RevokeMembershipBody,
    idempotency_key: str = Depends(get_idempotency_key),
    actor: User = Depends(require_roles(Role.ADMIN)),
):
    """撤销会员（B2-4，撤销交易边界已就绪后注册）。

    同一事务内撤会员 + 停用该用户自建私有策略启用实例、取消排队开仓、释放预留。
    响应含 membership 新态与 opening_risk_cleanup 摘要（停用实例/释放金额/待外部撤单）。
    """
    reason = _clean_reason(body.reason)
    fp = request_fingerprint(
        method="POST",
        route="/admin/entitlements/users/{user_id}/membership/revoke",
        action="membership.revoke",
        target_user_id=user_id,
        body={"reason": body.reason, "version": body.version},
    )
    ctx = _context(actor.user_id, idempotency_key, fp)
    return _run_write(lambda: _service().revoke_membership(
        context=ctx, target_user_id=user_id, reason=reason,
        expected_version=body.version,
    ))


# ── C-5 支付发放管理（list / retry / review，全 ADMIN） ──────────────────
@router.get("/payment-grants")
def list_payment_grants(
    params: Annotated[PaymentGrantsQueryParams, Query()],
    _: User = Depends(require_roles(Role.ADMIN)),
):
    """支付发放待办分页（默认 paid 未 granted；筛 user_id/grant_status/时间；≤200/页）。"""
    page_size = min(_PAYMENT_GRANTS_MAX_PAGE_SIZE, params.page_size)
    return _grant_service().list_payment_grants(
        user_id=params.user_id, grant_status=params.grant_status,
        from_at=params.from_at, to_at=params.to_at,
        page=params.page, page_size=page_size,
    )


@router.post("/payment-grants/{order_id}/retry")
def retry_payment_grant(
    order_id: str,
    body: RetryGrantBody,
    idempotency_key: str = Depends(get_idempotency_key),  # noqa: ARG001 — 防重复提交
    actor: User = Depends(require_roles(Role.ADMIN)),
):
    """管理员手动重试发放。granted 返当前；歧义须 review；活租约 409 in_progress。"""
    reason = _clean_reason(body.reason)
    return _run_grant(lambda: _grant_service().retry_grant(
        order_id=order_id, actor_user_id=actor.user_id, reason=reason,
    ))


@router.post("/payment-grants/{order_id}/review")
def review_payment_grant(
    order_id: str,
    body: ReviewGrantBody,
    idempotency_key: str = Depends(get_idempotency_key),  # noqa: ARG001 — 防重复提交
    actor: User = Depends(require_roles(Role.ADMIN)),
):
    """历史歧义订单复核：RETRY 转重试队列；MARK_GRANTED 对账标记（不发权益）。"""
    reason = _clean_reason(body.reason)
    return _run_grant(lambda: _grant_service().review_grant(
        order_id=order_id, actor_user_id=actor.user_id,
        decision=body.decision, reason=reason,
    ))
