"""BEpusdt 支付回调（P3，公开端点，无 get_current_user）。

BEpusdt 服务器 POST 通知到此；验签 + 幂等发放全在 PaymentService.handle_notify。
成功/重复 → 纯文本 ``ok``（BEpusdt 约定：收到 "ok" 即停止重推）；
验签失败/金额不足/未知单/状态非 paid → 400（BEpusdt 会重推，便于排查而非静默吞）。

安全：本路径无鉴权（网关不带 Bearer），但 handle_notify 的 md5 验签是准入闸；
若部署设了 INTERNAL_API_KEY，需把本路径加入 main._INTERNAL_KEY_EXEMPT（网关不带该头）。
viewer_readonly / user_owner_scope_only 中间件仅对携带 Bearer 的 VIEWER/USER 生效，
无 Bearer 的网关请求本就放行。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from zhice_os_v2.application.services.payment_service import (
    PaymentError,
    PaymentService,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing-webhook"])


async def _parse_payload(request: Request) -> dict:
    """兼容 BEpusdt 的 JSON 或 form 编码回调。"""
    ctype = request.headers.get("content-type", "")
    if "application/json" in ctype:
        try:
            data = await request.json()
            if isinstance(data, dict):
                return data
        except Exception:  # noqa: BLE001
            pass
    form = await request.form()
    return {k: v for k, v in form.items()}


@router.post("/bepusdt/notify")
async def bepusdt_notify(request: Request):
    payload = await _parse_payload(request)
    try:
        result = PaymentService().handle_notify(payload)
    except PaymentError as e:
        logger.warning("BEpusdt 回调被拒 code=%s order=%s", e, payload.get("order_id"))
        return JSONResponse(status_code=400, content={"code": str(e)})
    return PlainTextResponse(result)
