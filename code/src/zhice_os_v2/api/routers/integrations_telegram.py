"""Telegram webhook：一次性 deep-link 绑定入口（Phase C Task 3）。

安全属性：
- ``X-Telegram-Bot-Api-Secret-Token`` 与 ``telegram_webhook_secret`` 做
  ``hmac.compare_digest`` 比对；secret 缺失/错误/**未配置**或 binding 关闭
  一律 403（fail-closed，不接受无 secret 的 webhook）；
- 用户身份**只**来自 challenge 的 token_hash→user_id 映射，绝不信任 update
  内容里的用户信息；
- 仅处理 private chat 的 ``/start <token>`` 文本；其余 update（channel_post、
  edited_message、群组等）→ 200 直接忽略（Telegram 要求 webhook 除鉴权外
  永远 2xx）；
- 消费结果（bound/expired_or_used/chat_conflict）一律 200，不向 Telegram
  泄露内部状态；bound 时经现有 send_telegram transport best-effort 发一条
  确认消息（失败不影响响应）。
"""
from __future__ import annotations

import hmac

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict

from zhice_os_v2.application.services.telegram_binding_service import (
    TelegramBindingService,
)
from zhice_os_v2.application.services.telegram_notifier import send_telegram
from zhice_os_v2.config.settings import get_settings
from zhice_os_v2.infrastructure.repositories.telegram_binding_repository import (
    STATUS_BOUND,
)

router = APIRouter(prefix="/integrations", tags=["integrations-telegram"])

_WEBHOOK_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


class TelegramChat(BaseModel):
    """Telegram chat 对象（宽松结构，仅取绑定需要的字段）。"""

    model_config = ConfigDict(extra="ignore")
    id: int
    type: str | None = None


class TelegramMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    chat: TelegramChat | None = None
    text: str | None = None


class TelegramUpdate(BaseModel):
    """Telegram update（宽松结构：形态多变，仅取 message.chat/text）。"""

    model_config = ConfigDict(extra="ignore")
    update_id: int | None = None
    message: TelegramMessage | None = None


def _secret_ok(provided: str | None, expected: str) -> bool:
    """常量时间比对；expected 为空（未配置）时一律拒绝（fail-closed）。

    按 UTF-8 编码后比对：header 含非 ASCII 字节时 str 版 compare_digest 会抛
    TypeError 变 500，编码后保持 403 语义（复核 Low 修复）。
    """
    if not expected:
        return False
    return hmac.compare_digest((provided or "").encode("utf-8"), expected.encode("utf-8"))


def _parse_start(update: TelegramUpdate) -> tuple[str, str] | None:
    """仅接受 private chat 的 ``/start <token>``；其余返回 None（忽略）。

    返回 (token, chat_id)；chat_id 为数字串（private chat 的 id）。
    """
    msg = update.message
    if msg is None or msg.chat is None or msg.chat.type != "private":
        return None
    text = msg.text or ""
    if not text.startswith("/start "):
        return None
    token = text[len("/start "):].strip()
    if not token:
        return None
    return token, str(msg.chat.id)


@router.post("/telegram/webhook")
def telegram_webhook(
    update: TelegramUpdate,
    secret: str | None = Header(default=None, alias=_WEBHOOK_SECRET_HEADER),
) -> dict:
    settings = get_settings()
    if not settings.telegram_binding_enabled or not _secret_ok(
        secret, settings.telegram_webhook_secret
    ):
        raise HTTPException(status_code=403, detail="forbidden")

    parsed = _parse_start(update)
    if parsed is None:
        # 其余 update 一律 200 忽略（Telegram 要求 webhook 除鉴权外永远 2xx）
        return {"ok": True}
    token, chat_id = parsed

    # 用户身份只来自 challenge 映射；结果对 Telegram 一律 200，不泄露内部状态
    result = TelegramBindingService().consume(token, chat_id)
    if result == STATUS_BOUND:
        # best-effort 确认消息（经现有 broadcast transport；失败不影响 200）
        send_telegram(chat_id, "✅ ZHICE·OS：Telegram 绑定成功，后续通知将发送到本会话。")
    return {"ok": True}
