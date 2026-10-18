"""UserRepository: v2_user 表的 CRUD 操作。"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError

from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session


class UserRepository:
    def _s(self):
        return get_session()

    def create(self, user_id: str, username: str, password_hash: str, role: str,
               email: str | None = None) -> orm.UserModel:
        s = self._s()
        try:
            row = orm.UserModel(
                user_id=user_id,
                username=username,
                password_hash=password_hash,
                role=role,
                is_active=True,
                email=email,
                created_at=datetime.now(UTC),
            )
            s.add(row)
            s.commit()
            s.refresh(row)
            return row
        finally:
            s.close()

    def get_by_username(self, username: str) -> orm.UserModel | None:
        s = self._s()
        try:
            return s.query(orm.UserModel).filter_by(username=username, is_active=True).first()
        finally:
            s.close()

    def get_by_email(self, email: str) -> orm.UserModel | None:
        """按邮箱查（含 inactive，用于注册唯一性校验，避免 DB 唯一冲突）。"""
        s = self._s()
        try:
            return s.query(orm.UserModel).filter_by(email=email).first()
        finally:
            s.close()

    def get_by_id(self, user_id: str) -> orm.UserModel | None:
        s = self._s()
        try:
            return s.query(orm.UserModel).filter_by(user_id=user_id).first()
        finally:
            s.close()

    def list_all(self) -> list[orm.UserModel]:
        s = self._s()
        try:
            return s.query(orm.UserModel).order_by(orm.UserModel.created_at).all()
        finally:
            s.close()

    def update_telegram_chat_id(self, user_id: str, chat_id: str | None) -> bool:
        """Bind/revoke authoritative Telegram binding and legacy mirror atomically."""
        from zhice_os_v2.infrastructure.repositories.notification_binding_repository import (
            NotificationBindingRepository,
        )

        session = self._s()
        bindings = NotificationBindingRepository()
        now = datetime.now(UTC)
        try:
            user = (
                session.query(orm.UserModel)
                .filter_by(user_id=user_id)
                .with_for_update()
                .one_or_none()
            )
            if user is None:
                session.rollback()
                return False
            if chat_id is None:
                bindings.revoke(
                    session, user_id=user_id, channel="telegram", now=now
                )
            else:
                bindings.bind_verified(
                    session,
                    user_id=user_id,
                    channel="telegram",
                    recipient_id=chat_id,
                    now=now,
                )
            user.telegram_chat_id = chat_id
            session.flush([user])
            session.commit()
            return True
        except (IntegrityError, ValueError) as exc:
            session.rollback()
            if isinstance(exc, IntegrityError) or str(exc) == (
                "notification.binding_recipient_conflict"
            ):
                raise ValueError("telegram.chat_conflict") from None
            raise
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_active_notification_recipient(
        self, user_id: str, channel: str
    ) -> str | None:
        """Return only the active generic binding; never fall back to legacy columns."""
        session = self._s()
        try:
            row = (
                session.query(orm.UserNotificationBindingModel.recipient_id)
                .join(
                    orm.UserModel,
                    orm.UserModel.user_id
                    == orm.UserNotificationBindingModel.user_id,
                )
                .filter(
                    orm.UserNotificationBindingModel.user_id == user_id,
                    orm.UserNotificationBindingModel.channel == channel,
                    orm.UserNotificationBindingModel.status == "active",
                    orm.UserModel.is_active.is_(True),
                )
                .first()
            )
            return row[0] if row else None
        finally:
            session.close()

    def list_active_notification_recipients(
        self, channel: str, role: str | None = None
    ) -> list[str]:
        """List distinct recipients on active users and active generic bindings."""
        session = self._s()
        try:
            query = (
                session.query(orm.UserNotificationBindingModel.recipient_id)
                .join(
                    orm.UserModel,
                    orm.UserModel.user_id
                    == orm.UserNotificationBindingModel.user_id,
                )
                .filter(
                    orm.UserNotificationBindingModel.channel == channel,
                    orm.UserNotificationBindingModel.status == "active",
                    orm.UserModel.is_active.is_(True),
                )
            )
            if role is not None:
                query = query.filter(orm.UserModel.role == role)
            rows = query.distinct().order_by(
                orm.UserNotificationBindingModel.recipient_id.asc()
            ).all()
            return [row[0] for row in rows if row[0]]
        finally:
            session.close()

    def list_telegram_chat_ids(self) -> list[str]:
        """Legacy compatibility alias; SQL sending uses generic active bindings."""
        return self.list_active_notification_recipients("telegram")

    def update_locale(self, user_id: str, locale: str) -> bool:
        s = self._s()
        try:
            n = s.query(orm.UserModel).filter_by(user_id=user_id).update({"locale": locale})
            s.commit()
            return n > 0
        finally:
            s.close()

    def _mutate_access_protected(
        self,
        user_id: str,
        expected_auth_version: int | None = None,
        *,
        new_role: str | None = None,
        deactivate: bool = False,
    ) -> orm.UserModel:
        """角色变更 / 停用的统一原子事务（A3）。

        固定锁序（防并发互相降级死锁，见 test_auth_admin_concurrency_pg）：
          1) 按 id 升序锁定全部 active ADMIN；
          2) 再锁定目标行。
        PostgreSQL 用 FOR UPDATE 行锁串行化；SQLite 忽略锁但顺序执行（隔离测试用）。
        校验顺序：目标存在 → 乐观锁版本（expected_auth_version 非 None 时）→
        最后管理员保护 → 应用变更 → auth_version 递增一次。失败全回滚、不递增。
        """
        s = self._s()
        try:
            active_admins = (
                s.query(orm.UserModel)
                .filter_by(role="ADMIN", is_active=True)
                .order_by(orm.UserModel.id.asc())
                .with_for_update()
                .all()
            )
            row = (
                s.query(orm.UserModel)
                .filter_by(user_id=user_id)
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                # D10：code 化且不回显入参（auth.py 端点透传 {code} 走前端 i18n）
                raise ValueError("auth.user_not_found")
            if (
                expected_auth_version is not None
                and row.auth_version != expected_auth_version
            ):
                raise ValueError("auth.auth_version_conflict")
            removing_admin = row.role == "ADMIN" and row.is_active and (
                deactivate or (new_role is not None and new_role != "ADMIN")
            )
            if removing_admin and len(active_admins) <= 1:
                raise ValueError("auth.last_admin_protected")
            if new_role is not None:
                row.role = new_role
            if deactivate:
                row.is_active = False
            row.auth_version += 1
            s.commit()
            s.refresh(row)
            return row
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def update_role_protected(
        self, user_id: str, role: str, expected_auth_version: int | None = None
    ) -> orm.UserModel:
        """改角色（原子保护 + auth_version 递增）。返回更新后的行。"""
        return self._mutate_access_protected(
            user_id, expected_auth_version, new_role=role
        )

    def deactivate_protected(
        self, user_id: str, expected_auth_version: int | None = None
    ) -> orm.UserModel:
        """停用账户（原子保护 + auth_version 递增）。返回更新后的行。"""
        return self._mutate_access_protected(
            user_id, expected_auth_version, deactivate=True
        )

    def update_password(self, user_id: str, password_hash: str) -> orm.UserModel:
        """改密（3c：落 password_changed_at；A3：递增 auth_version 踢旧会话）。"""
        s = self._s()
        try:
            row = (
                s.query(orm.UserModel)
                .filter_by(user_id=user_id)
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                raise ValueError("auth.user_not_found")
            row.password_hash = password_hash
            row.password_changed_at = datetime.now(UTC)
            row.auth_version += 1
            s.commit()
            s.refresh(row)
            return row
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def count_admins(self) -> int:
        s = self._s()
        try:
            return s.query(orm.UserModel).filter_by(role="ADMIN", is_active=True).count()
        finally:
            s.close()
