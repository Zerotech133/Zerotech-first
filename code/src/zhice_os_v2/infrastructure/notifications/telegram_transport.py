"""现有 Telegram notifier 到统一通知 transport 契约的适配器。"""

from __future__ import annotations

from collections.abc import Callable

from zhice_os_v2.application.services.telegram_notifier import (
    TelegramSendResult,
    send_telegram,
)
from zhice_os_v2.domain.notification.contracts import RenderedMessage, SendResult

TelegramSender = Callable[[str, str], TelegramSendResult]


class TelegramTransport:
    """将 Telegram notifier 的结果逐字段映射为渠道无关结果。"""

    def __init__(self, sender: TelegramSender = send_telegram) -> None:
        self._sender = sender

    def send(self, recipient_id: str, message: RenderedMessage) -> SendResult:
        result = self._sender(recipient_id, message.html)
        return SendResult(
            ok=result.ok,
            status_code=result.status_code,
            error_code=result.error_code,
            retryable=result.retryable,
            recipient_invalid=result.recipient_invalid,
            retry_after=result.retry_after,
        )
