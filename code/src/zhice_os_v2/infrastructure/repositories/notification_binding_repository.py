"""Caller-session repository for channel-neutral notification bindings.

Every method participates in the caller's transaction and never commits, rolls back,
or closes the supplied session. User rows are the serialization point, so write paths
acquire locks in user -> binding/challenge order.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from zhice_os_v2.domain.notification.preference import SUPPORTED_NOTIFICATION_CHANNELS
from zhice_os_v2.infrastructure.db import models as orm

ConsumeStatus = Literal["bound", "already_bound", "expired_or_used"]
_RECIPIENT_CONFLICT = "notification.binding_recipient_conflict"
_UNSUPPORTED_CHANNEL = "notification.unsupported_channel"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _db_utc(value: datetime) -> datetime:
    """Store UTC wall time; SQLite drops tzinfo while PostgreSQL retains it."""
    return _as_utc(value)


def _ensure_sqlite_outer_transaction(session: Session) -> None:
    """Prevent a first SAVEPOINT from becoming the outer SQLite transaction."""
    if session.get_bind().dialect.name != "sqlite":
        return
    connection = session.connection()
    raw_connection = connection.connection.driver_connection
    if raw_connection is not None and not raw_connection.in_transaction:
        connection.exec_driver_sql("BEGIN")


def _validate_channel(channel: str) -> None:
    if channel not in SUPPORTED_NOTIFICATION_CHANNELS:
        raise ValueError(_UNSUPPORTED_CHANNEL)


class NotificationBindingRepository:
    """Atomic persistence operations using a caller-owned SQLAlchemy session."""

    @staticmethod
    def _lock_user(session: Session, user_id: str) -> orm.UserModel | None:
        return session.query(orm.UserModel).filter_by(user_id=user_id).with_for_update().first()

    @staticmethod
    def _binding_for_user(
        session: Session, user_id: str, channel: str, *, for_update: bool = False
    ) -> orm.UserNotificationBindingModel | None:
        query = session.query(orm.UserNotificationBindingModel).filter_by(
            user_id=user_id, channel=channel
        )
        if for_update:
            query = query.with_for_update()
        return query.first()

    @staticmethod
    def _recipient_owner(
        session: Session, channel: str, recipient_id: str
    ) -> orm.UserNotificationBindingModel | None:
        # Do not lock another user's binding after locking the target user/binding:
        # opposite cross-rebinds could otherwise deadlock. The unique constraint plus
        # savepoint below arbitrates any ownership race after this non-locking precheck.
        return (
            session.query(orm.UserNotificationBindingModel)
            .filter_by(channel=channel, recipient_id=recipient_id)
            .first()
        )

    def create_challenge(
        self,
        session: Session,
        *,
        user_id: str,
        channel: str,
        token: str,
        expires_at: datetime,
    ) -> orm.NotificationBindingChallengeModel:
        """Create a challenge after invalidating this user's prior open challenge."""
        _validate_channel(channel)
        user = self._lock_user(session, user_id)
        if user is None:
            raise LookupError("notification.binding_user_not_found")

        now = _utcnow()
        open_rows = (
            session.query(orm.NotificationBindingChallengeModel)
            .filter(
                orm.NotificationBindingChallengeModel.user_id == user_id,
                orm.NotificationBindingChallengeModel.channel == channel,
                orm.NotificationBindingChallengeModel.consumed_at.is_(None),
            )
            .with_for_update()
            .all()
        )
        for old in open_rows:
            old.consumed_at = now

        row = orm.NotificationBindingChallengeModel(
            challenge_id=f"nbc-{secrets.token_hex(16)}",
            user_id=user_id,
            channel=channel,
            token_hash=_hash_token(token),
            expires_at=_db_utc(expires_at),
            created_at=now,
        )
        session.add(row)
        session.flush()
        return row

    def set_provider_code(self, session: Session, challenge_id: str, provider_code: str) -> bool:
        """Set a provider code using the global user -> challenge lock order."""
        candidate = (
            session.query(orm.NotificationBindingChallengeModel.user_id)
            .filter_by(challenge_id=challenge_id)
            .first()
        )
        if candidate is None:
            return False
        if self._lock_user(session, candidate.user_id) is None:
            return False

        row = (
            session.query(orm.NotificationBindingChallengeModel)
            .filter(
                orm.NotificationBindingChallengeModel.challenge_id == challenge_id,
                orm.NotificationBindingChallengeModel.consumed_at.is_(None),
                orm.NotificationBindingChallengeModel.provider_code.is_(None),
            )
            .with_for_update()
            .first()
        )
        if row is None or _as_utc(row.expires_at) <= _utcnow():
            return False
        _ensure_sqlite_outer_transaction(session)
        try:
            with session.begin_nested():
                row.provider_code = provider_code
                session.flush([row])
        except IntegrityError:
            return False
        return True

    def consume_and_bind(
        self,
        session: Session,
        *,
        token: str,
        channel: str,
        recipient_id: str,
        now: datetime,
    ) -> ConsumeStatus:
        """Consume one valid challenge and atomically create or update its binding."""
        _validate_channel(channel)
        token_hash = _hash_token(token)
        candidate = (
            session.query(orm.NotificationBindingChallengeModel)
            .filter_by(token_hash=token_hash, channel=channel)
            .first()
        )
        if candidate is None:
            return "expired_or_used"
        if self._lock_user(session, candidate.user_id) is None:
            return "expired_or_used"

        challenge = (
            session.query(orm.NotificationBindingChallengeModel)
            .filter_by(token_hash=token_hash, channel=channel)
            .with_for_update()
            .first()
        )
        if (
            challenge is None
            or challenge.consumed_at is not None
            or _as_utc(challenge.expires_at) <= _as_utc(now)
        ):
            return "expired_or_used"

        _, already_bound = self._bind_locked(
            session,
            user_id=challenge.user_id,
            channel=channel,
            recipient_id=recipient_id,
            now=now,
            user_already_locked=True,
        )
        challenge.consumed_at = now
        session.flush([challenge])
        return "already_bound" if already_bound else "bound"

    def bind_verified(
        self,
        session: Session,
        *,
        user_id: str,
        channel: str,
        recipient_id: str,
        now: datetime,
    ) -> orm.UserNotificationBindingModel:
        """Bind an identity already verified by a trusted caller, without a challenge."""
        _validate_channel(channel)
        binding, _ = self._bind_locked(
            session,
            user_id=user_id,
            channel=channel,
            recipient_id=recipient_id,
            now=now,
            user_already_locked=False,
        )
        return binding

    def _bind_locked(
        self,
        session: Session,
        *,
        user_id: str,
        channel: str,
        recipient_id: str,
        now: datetime,
        user_already_locked: bool,
    ) -> tuple[orm.UserNotificationBindingModel, bool]:
        if not user_already_locked and self._lock_user(session, user_id) is None:
            raise LookupError("notification.binding_user_not_found")

        binding = self._binding_for_user(session, user_id, channel, for_update=True)
        if (
            binding is not None
            and binding.status == "active"
            and binding.recipient_id == recipient_id
        ):
            return binding, True

        owner = self._recipient_owner(session, channel, recipient_id)
        if owner is not None and owner.user_id != user_id:
            raise ValueError(_RECIPIENT_CONFLICT)

        # A nested savepoint translates a concurrent unique-recipient race without
        # poisoning or rolling back the caller's outer transaction.
        _ensure_sqlite_outer_transaction(session)
        try:
            with session.begin_nested():
                if binding is None:
                    binding = orm.UserNotificationBindingModel(
                        binding_id=f"nbind-{secrets.token_hex(16)}",
                        user_id=user_id,
                        channel=channel,
                        recipient_id=recipient_id,
                        status="active",
                        bound_at=now,
                        revoked_at=None,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(binding)
                else:
                    binding.recipient_id = recipient_id
                    binding.status = "active"
                    binding.bound_at = now
                    binding.revoked_at = None
                    binding.updated_at = now
                session.flush([binding])
        except IntegrityError:
            raise ValueError(_RECIPIENT_CONFLICT) from None
        return binding, False

    def get_active(
        self, session: Session, user_id: str, channel: str
    ) -> orm.UserNotificationBindingModel | None:
        _validate_channel(channel)
        return (
            session.query(orm.UserNotificationBindingModel)
            .filter_by(user_id=user_id, channel=channel, status="active")
            .first()
        )

    def list_for_user(
        self, session: Session, user_id: str
    ) -> list[orm.UserNotificationBindingModel]:
        return (
            session.query(orm.UserNotificationBindingModel)
            .filter_by(user_id=user_id)
            .order_by(orm.UserNotificationBindingModel.channel.asc())
            .all()
        )

    def revoke(
        self,
        session: Session,
        *,
        user_id: str,
        channel: str,
        now: datetime,
    ) -> bool:
        """Revoke the active binding and invalidate every open challenge."""
        _validate_channel(channel)
        if self._lock_user(session, user_id) is None:
            return False
        binding = self._binding_for_user(session, user_id, channel, for_update=True)
        changed = binding is not None and binding.status == "active"
        if binding is not None and changed:
            binding.status = "revoked"
            binding.revoked_at = now
            binding.updated_at = now

        challenges = (
            session.query(orm.NotificationBindingChallengeModel)
            .filter(
                orm.NotificationBindingChallengeModel.user_id == user_id,
                orm.NotificationBindingChallengeModel.channel == channel,
                orm.NotificationBindingChallengeModel.consumed_at.is_(None),
            )
            .with_for_update()
            .all()
        )
        for challenge in challenges:
            challenge.consumed_at = now
        session.flush()
        return changed

    def revoke_if_current(
        self,
        session: Session,
        user_id: str,
        channel: str,
        recipient_id: str,
        now: datetime,
    ) -> bool:
        """Revoke only if an asynchronous result still names the current recipient."""
        _validate_channel(channel)
        if self._lock_user(session, user_id) is None:
            return False
        binding = self._binding_for_user(session, user_id, channel, for_update=True)
        if binding is None or binding.status != "active" or binding.recipient_id != recipient_id:
            return False
        binding.status = "revoked"
        binding.revoked_at = now
        binding.updated_at = now
        session.flush([binding])
        return True
