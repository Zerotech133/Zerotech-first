"""WxPusher 官方回调 webhook：/integrations/wxpusher/webhook/{path_key}。

安全属性（顺序固定）：
1. path_key 与 wxpusher_webhook_path_key 常量时间比对（bytes 形式——对含非 ASCII 的
   任意内容恒不抛；不匹配 404 且与未知路由同形，不泄露存在性；未配置 path_key 恒
   404，fail-closed）。请求体在通过 path_key 检查前绝不读取/解析，杜绝 FastAPI 422
   回显请求体；
2. body 就地解析（坏 JSON → 403 稳定码，不回显原文）后 verifier 验签（appId 精确
   相等 + appKey 常量时间；失败/非 dict 一律 403 稳定码）；
3. parser 归一化（畸形/未知 action 400 稳定码，绝不回显 payload）；
4. service 处理后一律 204 空 body（成功/重复/过期 challenge 不区分，防探测）。

错误响应只 {detail:{code}}，绝不回显请求体/uid/appKey；path_key 不落应用日志，
nginx 侧该 location 已 access_log off。
"""
from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from zhice_os_v2.api.dependencies import get_notification_binding_service
from zhice_os_v2.application.services.notification_binding_service import (
    NotificationBindingService,
)
from zhice_os_v2.config.settings import get_settings
from zhice_os_v2.infrastructure.notifications.wxpusher_webhook import (
    WxPusherWebhookError,
    WxPusherWebhookVerifier,
)

router = APIRouter(
    prefix="/integrations/wxpusher",
    tags=["integrations-wxpusher"],
    include_in_schema=False,
)


@router.post("/webhook/{path_key}", status_code=204)
async def wxpusher_webhook(
    path_key: str,
    request: Request,
    service: NotificationBindingService = Depends(get_notification_binding_service),
) -> Response:
    expected = get_settings().wxpusher_webhook_path_key
    # 未配置（空）恒 404；bytes 形式 compare_digest 对任意内容（含非 ASCII）恒不抛，
    # 404 与未知路由同形不泄露存在性
    if not expected or not hmac.compare_digest(
        path_key.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(status_code=404, detail="Not Found")
    # body 只在 path_key 通过后读取：坏 JSON 无法验签，按验签失败处理（403 稳定码，
    # 绝不回显原文）
    try:
        event = await request.json()
    except Exception:
        raise HTTPException(
            status_code=403, detail={"code": "wxpusher.webhook_forbidden"}
        ) from None
    settings = get_settings()
    if not WxPusherWebhookVerifier(
        app_id=settings.wxpusher_app_id, app_key=settings.wxpusher_app_key
    ).verify(event):
        raise HTTPException(status_code=403, detail={"code": "wxpusher.webhook_forbidden"})
    try:
        service.handle_webhook_event(event)
    except WxPusherWebhookError as exc:
        # parser 稳定错误码（如 wxpusher_webhook_invalid_payload / ..._unknown_action）
        raise HTTPException(status_code=400, detail={"code": str(exc)}) from None
    return Response(status_code=204)
