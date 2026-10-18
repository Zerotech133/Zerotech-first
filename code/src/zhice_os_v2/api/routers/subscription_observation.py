"""订阅者只读观察平台策略产出。订阅校验为唯一授权门（放开了 trades.py 对内置成交的
合规红线——仅限本人订阅的策略，见 spec 合规决策）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from zhice_os_v2.api.dependencies import get_current_user
from zhice_os_v2.application.services.membership_service import SubscriptionService
from zhice_os_v2.application.services.subscription_observation_service import (
    SubscriptionObservationService,
)
from zhice_os_v2.domain.security.user import User

router = APIRouter(prefix="/my/subscriptions", tags=["subscription-observation"])


def _require_subscribed(user: User, strategy_id: str) -> None:
    if not SubscriptionService().is_subscribed(user.user_id, strategy_id):
        raise HTTPException(status_code=403, detail={"code": "billing.not_subscribed"})


@router.get("/{strategy_id}/positions")
def subscription_positions(strategy_id: str, user: User = Depends(get_current_user)):
    _require_subscribed(user, strategy_id)
    return SubscriptionObservationService().list_platform_positions(strategy_id)


@router.get("/{strategy_id}/trades")
def subscription_trades(strategy_id: str, user: User = Depends(get_current_user)):
    _require_subscribed(user, strategy_id)
    return SubscriptionObservationService().list_platform_trades(strategy_id)
