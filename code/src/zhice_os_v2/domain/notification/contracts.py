"""渠道无关的通知领域契约。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class NotificationChannel(StrEnum):
    TELEGRAM = "telegram"
    WXPUSHER = "wxpusher"


class BindingStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


@dataclass(frozen=True)
class RenderedMessage:
    summary: str
    text: str
    html: str
    url: str | None = None


@dataclass(frozen=True)
class SendResult:
    ok: bool
    status_code: int | None = None
    error_code: str | None = None
    retryable: bool = False
    recipient_invalid: bool = False
    retry_after: int | None = None


class NotificationTransport(Protocol):
    def send(self, recipient_id: str, message: RenderedMessage) -> SendResult: ...


def mask_recipient(value: str) -> str:
    """仅保留收件人标识末四位；WxPusher UID 保留固定前缀。"""
    value = value.strip()
    if len(value) <= 4:
        return "*" * len(value)
    if value.startswith("UID_"):
        return f"UID_***{value[-4:]}"
    return f"{'*' * (len(value) - 4)}{value[-4:]}"
