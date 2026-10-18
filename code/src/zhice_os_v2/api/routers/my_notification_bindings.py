"""本人通知绑定 API：/auth/me/notification-bindings。

安全属性：
- user_id 一律取自 get_current_user/require_roles，任何 body/query 的 user_id 恒被无视
  （无跨账号标识符）；
- challenge 响应只有二维码图片与过期时刻（cache-control: no-store），token/provider_code
  绝不返回；
- VIEWER/USER/ADMIN 均可自助维护（写方法已入 main._VIEWER_WRITE_ALLOW 前缀白名单；
  PATCH/PUT 不在白名单，仍被 VIEWER 只读中间件拒绝）。
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from zhice_os_v2.api.dependencies import (
    get_current_user,
    get_notification_binding_service,
    require_roles,
)
from zhice_os_v2.application.services.notification_binding_service import (
    BINDING_DISABLED_CODE,
    BINDING_REQUIRED_CODE,
    QRCODE_FAILED_CODE,
    BindingView,
    NotificationBindingService,
)
from zhice_os_v2.config.settings import get_settings
from zhice_os_v2.domain.security.user import Role, User

router = APIRouter(
    prefix="/auth/me/notification-bindings",
    tags=["my-notification-bindings"],
    dependencies=[Depends(get_current_user)],
)

# 通知绑定属于自助设置，VIEWER 也可维护；身份恒取 JWT。
_OWNER_ROLES = (Role.VIEWER, Role.USER, Role.ADMIN)


@router.post("/wxpusher/challenge")
def create_wxpusher_challenge(
    user: User = Depends(require_roles(*_OWNER_ROLES)),
    service: NotificationBindingService = Depends(get_notification_binding_service),
):
    """创建 WxPusher 参数二维码绑定 challenge（fail-closed：未启用即 503 稳定码）。"""
    if not get_settings().wxpusher_binding_enabled:
        raise HTTPException(status_code=503, detail={"code": BINDING_DISABLED_CODE})
    try:
        view = service.create_wxpusher_challenge(user.user_id)
    except ValueError as exc:
        code = str(exc)
        status = 502 if code == QRCODE_FAILED_CODE else 503
        raise HTTPException(status_code=status, detail={"code": code}) from exc
    return JSONResponse(
        status_code=200,
        content=jsonable_encoder(view),
        headers={"cache-control": "no-store"},
    )


@router.get("")
def list_my_notification_bindings(
    user: User = Depends(require_roles(*_OWNER_ROLES)),
    service: NotificationBindingService = Depends(get_notification_binding_service),
):
    """列出本人各渠道绑定（先触发 wxpusher 轮询闭环兜底；recipient 恒掩码）。"""
    views: list[BindingView] = service.list_bindings(user.user_id)
    return [
        {
            "channel": view.channel,
            "status": view.status,
            "recipient_masked": view.recipient_masked,
            "bound_at": view.bound_at,
        }
        for view in views
    ]


@router.delete("/wxpusher")
def delete_wxpusher_binding(
    user: User = Depends(require_roles(*_OWNER_ROLES)),
    service: NotificationBindingService = Depends(get_notification_binding_service),
):
    """本人解绑 wxpusher（caller 传入的任何 user_id query 参数一律无视）。"""
    service.unbind(user.user_id)
    return {"ok": True}


@router.post("/wxpusher/test")
def send_wxpusher_test(
    user: User = Depends(require_roles(*_OWNER_ROLES)),
    service: NotificationBindingService = Depends(get_notification_binding_service),
    body: dict | None = Body(default=None),
):
    """向本人 active 绑定发测试消息（body 恒被无视；响应只有 {ok, reason?}）。"""
    try:
        return service.send_test_message(user.user_id)
    except ValueError as exc:
        if str(exc) == BINDING_REQUIRED_CODE:
            raise HTTPException(
                status_code=409, detail={"code": BINDING_REQUIRED_CODE}
            ) from exc
        raise
