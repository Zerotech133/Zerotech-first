"""计费/权益 用户侧 API（P1）：/billing。

- GET /billing/plans          价格表（会员月费 + 策略订阅默认价 + 覆盖价）。登录可读。
- GET /billing/membership     当前用户会员态（tier/valid_until/is_member）。
- GET /billing/subscriptions  当前用户的策略订阅列表（含是否有效）。

付费自助购买入口（下单/收银台）属 P3；本轮仅暴露展示与查询。发放走 admin_billing。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from zhice_os_v2.api.dependencies import get_current_user
from zhice_os_v2.application.services.membership_service import (
    MembershipService,
    SubscriptionService,
    get_pricing,
)
from zhice_os_v2.application.services.payment_service import (
    PaymentError,
    PaymentService,
)
from zhice_os_v2.domain.security.user import User

router = APIRouter(
    prefix="/billing", tags=["billing"], dependencies=[Depends(get_current_user)]
)


class CreateOrderBody(BaseModel):
    product_type: str = Field(pattern="^(membership|subscription)$")
    product_ref: str | None = None  # subscription: 内置 strategy_id
    months: int = Field(ge=1, le=120)


@router.get("/plans")
def get_plans():
    """价格表（USDT/月）。会员与策略订阅均按月计费。"""
    p = get_pricing()
    return {
        "currency": "USDT",
        "period": "monthly",
        "membership_monthly_usdt": p.get("membership_monthly_usdt"),
        "strategy_default_monthly_usdt": p.get("strategy_default_monthly_usdt"),
        "strategy_overrides": p.get("strategy_overrides", {}),
    }


@router.get("/membership")
def my_membership(current_user: User = Depends(get_current_user)):
    return MembershipService().status_view(current_user.user_id)


@router.get("/subscriptions")
def my_subscriptions(current_user: User = Depends(get_current_user)):
    return {"items": SubscriptionService().list_for_user(current_user.user_id)}


@router.get("/strategies")
def grantable_strategies():
    """当前可授权平台策略候选（B1-5 统一谓词）：owner 空 + PUBLIC/PUBLISHED/ACTIVE + 启用市场。

    支付下单、管理员授权、前端订阅页共用同一资格判定；按 (market, strategy_id) 稳定排序。
    """
    from zhice_os_v2.api.dependencies import get_db
    from zhice_os_v2.application.services.strategy_catalog_service import (
        StrategyCatalogService,
    )
    from zhice_os_v2.config.settings import get_settings
    from zhice_os_v2.domain.strategy.eligibility import (
        deployment_enabled_markets,
        is_grantable_platform_strategy,
    )

    enabled = deployment_enabled_markets(get_settings().scheduler_markets)
    items = [
        {"strategy_id": s.strategy_id, "strategy_name": s.strategy_name,
         "market": s.market}
        for s in StrategyCatalogService(get_db()).list_all()
        if is_grantable_platform_strategy(s, enabled_markets=enabled)
    ]
    items.sort(key=lambda x: (x["market"], x["strategy_id"]))
    return {"items": items}


# ---------------- 自助下单 / 订单查询（P3）----------------
@router.post("/orders")
def create_order(
    body: CreateOrderBody, current_user: User = Depends(get_current_user)
):
    """下单买会员/订阅 → 返回 BEpusdt 收银台（payment_url/收款地址/应付 USDT/过期）。"""
    try:
        return PaymentService().create_order(
            current_user,
            product_type=body.product_type,
            product_ref=body.product_ref,
            months=body.months,
        )
    except PaymentError as e:
        raise HTTPException(status_code=400, detail={"code": str(e)})


@router.get("/orders")
def list_orders(current_user: User = Depends(get_current_user)):
    return {"items": PaymentService().list_orders(current_user.user_id)}


@router.get("/orders/{order_id}")
def get_order(order_id: str, current_user: User = Depends(get_current_user)):
    """owner-scoped 查单（供前端轮询支付状态）。非本人订单一律 404。"""
    o = PaymentService().get_order(order_id, current_user.user_id)
    if o is None:
        raise HTTPException(status_code=404, detail={"code": "billing.order_not_found"})
    return o
