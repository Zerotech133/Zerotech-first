"""owner-scoped 用户通知偏好 API：/my/notification-preferences。

owner 恒取自 JWT current_user（GET 需登录；PATCH 限 USER/ADMIN，与其它 /my 写
一致），请求体绝不接受 user_id（防越权归属）。strict 校验全部在
NotificationPreferenceBody（未知字段/市场/静音段/时区/locale 非法一律 422）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from zhice_os_v2.api.dependencies import (
    get_current_user,
    get_notification_preference_service,
    require_roles,
)
from zhice_os_v2.application.services.notification_preference_service import (
    NotificationPreferenceBody,
    NotificationPreferenceService,
)
from zhice_os_v2.domain.notification.preference import (
    NOTIFICATION_CHANNEL_TELEGRAM,
    SUPPORTED_NOTIFICATION_CHANNELS,
    UserNotificationPreference,
)
from zhice_os_v2.domain.security.user import Role, User

router = APIRouter(
    prefix="/my/notification-preferences",
    tags=["my-notification-preferences"],
    dependencies=[Depends(get_current_user)],
)

# 本人通知偏好属于自助安全设置，VIEWER 也可维护；owner 恒取 JWT。
_OWNER_WRITE_ROLES = (Role.VIEWER, Role.USER, Role.ADMIN)


def _serialize(p: UserNotificationPreference) -> dict:
    return {
        "user_id": p.user_id,
        "channel": p.channel,
        "market_reports_enabled": p.market_reports_enabled,
        "signal_enabled": p.signal_enabled,
        "fill_enabled": p.fill_enabled,
        "morning_report_enabled": p.morning_report_enabled,
        "evening_report_enabled": p.evening_report_enabled,
        "system_alerts_enabled": p.system_alerts_enabled,
        "quiet_hours": p.quiet_hours,
        "timezone": p.timezone,
        "locale": p.locale,
    }


def _validate_channel(channel: str) -> str:
    if channel not in SUPPORTED_NOTIFICATION_CHANNELS:
        raise HTTPException(
            status_code=422, detail={"code": "notification.channel_invalid"}
        )
    return channel


@router.get("")
def get_my_notification_preferences(
    channel: str = NOTIFICATION_CHANNEL_TELEGRAM,
    user: User = Depends(get_current_user),
    service: NotificationPreferenceService = Depends(
        get_notification_preference_service
    ),
):
    """返回本人偏好（无则按缺省创建后返回；幂等，行只属本人）。"""
    return _serialize(service.get_or_create(user.user_id, _validate_channel(channel)))


@router.patch("")
def patch_my_notification_preferences(
    body: NotificationPreferenceBody,
    channel: str = NOTIFICATION_CHANNEL_TELEGRAM,
    user: User = Depends(require_roles(*_OWNER_WRITE_ROLES)),
    service: NotificationPreferenceService = Depends(
        get_notification_preference_service
    ),
):
    """更新本人偏好（strict body；None=不改，市场开关按键合并）。"""
    try:
        preference = service.update(user.user_id, body, _validate_channel(channel))
    except ValueError as exc:
        if str(exc) == "notification.binding_required":
            raise HTTPException(
                status_code=409, detail={"code": "notification.binding_required"}
            ) from exc
        raise
    return _serialize(preference)
