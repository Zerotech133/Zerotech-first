"""Telegram 绑定服务：一次性 deep-link challenge 生命周期（Phase C Task 3）。

安全铁律：
- token 用 ``secrets.token_urlsafe(32)`` 生成，**只在创建响应里一次性**返回给发起
  绑定的用户本人；DB 只落 SHA-256 哈希，日志/错误响应绝不带 token 原文；
- fail-closed：``telegram_binding_enabled=False``（默认）时创建 challenge 直接
  拒绝（ValueError，错误码 ``telegram.binding_disabled``），不产生任何行；
- 消费时用户身份只来自 challenge 行的 token_hash→user_id 映射，绝不信任
  Telegram update 内容里的用户信息；
- 现有广播行为不受影响（telegram_notifier 是独立模块，Task 6 才降级为 transport）。
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from zhice_os_v2.config.settings import get_settings
from zhice_os_v2.domain.security.user import User
from zhice_os_v2.infrastructure.repositories.telegram_binding_repository import (
    ConsumeStatus,
    TelegramBindingRepository,
)


def _utc(dt: datetime) -> datetime:
    """读回的 expires_at 统一为 aware-UTC（sqlite 丢 tzinfo，PG 保留）。"""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)

#: binding 未开启时 create_challenge 抛的错误码（HTTP 层透传给前端 i18n）
BINDING_DISABLED_CODE = "telegram.binding_disabled"


@dataclass(frozen=True)
class BindingChallenge:
    """创建结果：token/url 仅此一次返回，绝不持久化、绝不写日志。"""

    challenge_id: str
    token: str
    url: str
    expires_at: datetime


class TelegramBindingService:
    def __init__(self, repo: TelegramBindingRepository | None = None) -> None:
        self.repo = repo or TelegramBindingRepository()

    def create_challenge(self, user: User) -> BindingChallenge:
        """为用户创建一次性绑定 challenge（自动作废旧未消费 challenge）。

        fail-closed：binding 未开启时抛 ValueError(BINDING_DISABLED_CODE)。
        """
        settings = get_settings()
        if not settings.telegram_binding_enabled:
            raise ValueError(BINDING_DISABLED_CODE)
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + timedelta(
            seconds=settings.telegram_challenge_ttl_seconds
        )
        # Generic repository hashes the token before persistence and invalidates prior rows.
        row = self.repo.create(
            user_id=user.user_id,
            token=token,
            expires_at=expires_at,
        )
        url = f"https://t.me/{settings.telegram_bot_username}?start={token}"
        return BindingChallenge(
            challenge_id=row.challenge_id,
            token=token,
            url=url,
            expires_at=_utc(row.expires_at),
        )

    def consume(self, token: str, chat_id: str) -> ConsumeStatus:
        """按 token 消费 challenge：成功则把 chat_id 绑到 challenge 的 user_id。

        返回三种状态码（bound / expired_or_used / chat_conflict），细节见仓储。
        """
        return self.repo.consume(token.strip(), str(chat_id).strip())
