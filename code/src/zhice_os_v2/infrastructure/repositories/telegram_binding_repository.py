"""Telegram compatibility facade over channel-neutral notification bindings.

The generic tables are authoritative. The legacy ``v2_user.telegram_chat_id`` column is
updated only as a same-transaction compatibility mirror.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.exc import IntegrityError

from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session
from zhice_os_v2.infrastructure.repositories.notification_binding_repository import (
    NotificationBindingRepository,
)

ConsumeStatus = Literal[
    "bound", "already_bound", "expired_or_used", "chat_conflict"
]

STATUS_BOUND: ConsumeStatus = "bound"
STATUS_ALREADY_BOUND: ConsumeStatus = "already_bound"
STATUS_EXPIRED_OR_USED: ConsumeStatus = "expired_or_used"
STATUS_CHAT_CONFLICT: ConsumeStatus = "chat_conflict"


def hash_token(token: str) -> str:
    """Token plaintext to SHA-256 hex; retained for compatibility tests/tools."""
    return hashlib.sha256(token.encode()).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TelegramBindingRepository:
    """Own transactions at the Telegram boundary; generic repository never commits."""

    def __init__(self) -> None:
        self._bindings = NotificationBindingRepository()

    def _s(self):
        return get_session()

    def create(
        self,
        *,
        user_id: str,
        token: str,
        expires_at: datetime,
    ) -> orm.NotificationBindingChallengeModel:
        session = self._s()
        try:
            row = self._bindings.create_challenge(
                session,
                user_id=user_id,
                channel="telegram",
                token=token,
                expires_at=expires_at,
            )
            session.commit()
            session.refresh(row)
            return row
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def latest_for_user(
        self, user_id: str
    ) -> orm.NotificationBindingChallengeModel | None:
        session = self._s()
        try:
            return (
                session.query(orm.NotificationBindingChallengeModel)
                .filter_by(user_id=user_id, channel="telegram")
                .order_by(orm.NotificationBindingChallengeModel.id.desc())
                .first()
            )
        finally:
            session.close()

    def get_by_token_hash(
        self, token_hash: str
    ) -> orm.NotificationBindingChallengeModel | None:
        session = self._s()
        try:
            return (
                session.query(orm.NotificationBindingChallengeModel)
                .filter_by(token_hash=token_hash, channel="telegram")
                .first()
            )
        finally:
            session.close()

    def consume(self, token: str, chat_id: str) -> ConsumeStatus:
        """Consume and mirror within one transaction, preserving stable Telegram codes."""
        session = self._s()
        try:
            result = self._bindings.consume_and_bind(
                session,
                token=token,
                channel="telegram",
                recipient_id=chat_id,
                now=_utcnow(),
            )
            if result == STATUS_EXPIRED_OR_USED:
                session.rollback()
                return STATUS_EXPIRED_OR_USED

            challenge = (
                session.query(orm.NotificationBindingChallengeModel)
                .filter_by(token_hash=hash_token(token), channel="telegram")
                .first()
            )
            if challenge is None:
                session.rollback()
                return STATUS_EXPIRED_OR_USED
            user = session.query(orm.UserModel).filter_by(user_id=challenge.user_id).one()
            user.telegram_chat_id = chat_id
            session.flush([user])
            session.commit()
            # A fresh Telegram challenge historically confirms success even when the
            # recipient was already the user's active binding. Keep that public
            # contract at this compatibility boundary; generic callers retain the
            # more precise ``already_bound`` result.
            return STATUS_BOUND if result == STATUS_ALREADY_BOUND else result
        except ValueError as exc:
            session.rollback()
            if str(exc) == "notification.binding_recipient_conflict":
                return STATUS_CHAT_CONFLICT
            raise
        except IntegrityError:
            session.rollback()
            return STATUS_CHAT_CONFLICT
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
