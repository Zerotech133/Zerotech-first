"""NotificationPreferenceRepository: v2_user_notification_preference 表 CRUD。

查询恒带 user_id+channel 边界（每用户每渠道一行）。create/update 可加入调用方
事务；未传 session 时保持原有的自管 session/commit/close 兼容行为。create 捕获并发
IntegrityError（unique(user_id,channel) 兜底）返回 None，由服务层重读保证幂等。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from zhice_os_v2.domain.notification.preference import UserNotificationPreference
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _ensure_sqlite_outer_transaction(session: Session) -> None:
    """Prevent a first SAVEPOINT from becoming the outer SQLite transaction."""
    if session.get_bind().dialect.name != "sqlite":
        return
    connection = session.connection()
    raw_connection = connection.connection.driver_connection
    if raw_connection is not None and not raw_connection.in_transaction:
        connection.exec_driver_sql("BEGIN")


def _to_domain(row: orm.UserNotificationPreferenceModel) -> UserNotificationPreference:
    return UserNotificationPreference(
        preference_id=row.preference_id,
        user_id=row.user_id,
        channel=row.channel,
        market_reports_enabled=dict(row.market_reports_enabled or {}),
        signal_enabled=bool(row.signal_enabled),
        fill_enabled=bool(row.fill_enabled),
        morning_report_enabled=bool(row.morning_report_enabled),
        evening_report_enabled=bool(row.evening_report_enabled),
        system_alerts_enabled=bool(row.system_alerts_enabled),
        quiet_hours=dict(row.quiet_hours) if row.quiet_hours else None,
        timezone=row.timezone or "Asia/Shanghai",
        locale=row.locale or "zh",
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class NotificationPreferenceRepository:
    def _s(self):
        return get_session()

    def get(self, user_id: str, channel: str = "telegram") -> UserNotificationPreference | None:
        s = self._s()
        try:
            row = (
                s.query(orm.UserNotificationPreferenceModel)
                .filter_by(user_id=user_id, channel=channel)
                .first()
            )
            return _to_domain(row) if row is not None else None
        finally:
            s.close()

    def get_many(
        self, user_ids: list[str], channel: str = "telegram"
    ) -> dict[str, UserNotificationPreference]:
        """批量取偏好（report_recipients 用；返回 user_id → 偏好映射，缺行不在键中）。"""
        s = self._s()
        try:
            if not user_ids:
                return {}
            rows = (
                s.query(orm.UserNotificationPreferenceModel)
                .filter(
                    orm.UserNotificationPreferenceModel.user_id.in_(user_ids),
                    orm.UserNotificationPreferenceModel.channel == channel,
                )
                .all()
            )
            return {r.user_id: _to_domain(r) for r in rows}
        finally:
            s.close()

    def user_locale(self, user_id: str) -> str | None:
        """用户 locale（get_or_create 缺省创建用；无用户行返回 None 由服务层回退 zh）。"""
        s = self._s()
        try:
            row = s.query(orm.UserModel).filter_by(user_id=user_id).first()
            return (row.locale or None) if row is not None else None
        finally:
            s.close()

    def has_active_binding(self, user_id: str, channel: str) -> bool:
        """Read the authoritative generic binding table for the channel gate."""
        s = self._s()
        try:
            return (
                s.query(orm.UserNotificationBindingModel.id)
                .filter_by(user_id=user_id, channel=channel, status="active")
                .first()
                is not None
            )
        finally:
            s.close()

    def create(
        self,
        preference_id: str,
        user_id: str,
        *,
        channel: str = "telegram",
        locale: str = "zh",
        session: Session | None = None,
    ) -> UserNotificationPreference | None:
        """插入缺省偏好；caller session 模式不 commit/rollback/close。"""
        owns_session = session is None
        s = session or self._s()
        try:
            row = orm.UserNotificationPreferenceModel(
                preference_id=preference_id,
                user_id=user_id,
                channel=channel,
                market_reports_enabled={},
                signal_enabled=True,
                fill_enabled=True,
                morning_report_enabled=True,
                evening_report_enabled=True,
                system_alerts_enabled=True,
                quiet_hours=None,
                timezone="Asia/Shanghai",
                locale=locale,
                created_at=_utcnow(),
                updated_at=_utcnow(),
            )
            if owns_session:
                s.add(row)
                try:
                    s.commit()
                except IntegrityError:
                    s.rollback()
                    return None
                s.refresh(row)
            else:
                # SAVEPOINT keeps a duplicate-create race from poisoning the caller's
                # outer transaction while preserving the legacy None contract.
                _ensure_sqlite_outer_transaction(s)
                try:
                    with s.begin_nested():
                        s.add(row)
                        s.flush([row])
                except IntegrityError:
                    return None
            return _to_domain(row)
        finally:
            if owns_session:
                s.close()

    def update(
        self,
        user_id: str,
        *,
        channel: str = "telegram",
        fields: dict,
        session: Session | None = None,
    ) -> UserNotificationPreference | None:
        """按白名单回写偏好；caller session 模式不 commit/rollback/close。"""
        allowed = {
            "market_reports_enabled",
            "signal_enabled",
            "fill_enabled",
            "morning_report_enabled",
            "evening_report_enabled",
            "system_alerts_enabled",
            "quiet_hours",
            "timezone",
            "locale",
        }
        owns_session = session is None
        s = session or self._s()
        try:
            row = (
                s.query(orm.UserNotificationPreferenceModel)
                .filter_by(user_id=user_id, channel=channel)
                .first()
            )
            if row is None:
                return None
            for key, value in fields.items():
                if key in allowed:
                    setattr(row, key, value)
            if fields:
                row.updated_at = _utcnow()
            if owns_session:
                s.commit()
                s.refresh(row)
            else:
                s.flush([row])
            return _to_domain(row)
        finally:
            if owns_session:
                s.close()
