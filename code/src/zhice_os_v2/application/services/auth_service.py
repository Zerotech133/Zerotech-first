"""认证服务。repo=None 时内存模式（测试）；repo=UserRepository() 时 DB 模式（生产）。"""
from __future__ import annotations

import secrets

from email_validator import EmailNotValidError, validate_email

from zhice_os_v2.domain.security.user import Role, User


class AuthService:
    def __init__(self, repo=None) -> None:
        self._repo = repo
        self._mem: dict[str, tuple[User, str]] = {}
        if repo is None:
            # 内存模式：自动 seed admin（兼容旧行为）
            from zhice_os_v2.config.settings import get_settings
            pw = get_settings().admin_password or ""
            self._mem["admin"] = (User(user_id="u-admin", username="admin", role=Role.ADMIN), pw)

    def _db(self) -> bool:
        return self._repo is not None

    def _row(self, row) -> User:
        return User(user_id=row.user_id, username=row.username, role=Role(row.role),
                    is_active=row.is_active, locale=getattr(row, "locale", "zh") or "zh",
                    email=getattr(row, "email", None),
                    telegram_chat_id=getattr(row, "telegram_chat_id", None),
                    auth_version=getattr(row, "auth_version", 1))

    def set_locale(self, user_id: str, locale: str) -> None:
        """更新用户语言偏好（sql 后端持久化；内存后端更新对象）。locale 限 zh/en。"""
        loc = locale if locale in ("zh", "en") else "zh"
        if self._db():
            self._repo.update_locale(user_id, loc)
        else:
            for user, _ in self._mem.values():
                if user.user_id == user_id:
                    user.locale = loc

    def set_telegram_chat_id(self, user_id: str, chat_id: str | None) -> None:
        """绑定/解绑 Telegram chat_id。chat_id 须为数字串(可负,群组)或空(解绑)。"""
        cid = (chat_id or "").strip()
        if cid and not cid.lstrip("-").isdigit():
            raise ValueError("auth.invalid_chat_id")
        stored = cid or None
        if self._db():
            self._repo.update_telegram_chat_id(user_id, stored)
        else:
            for user, _ in self._mem.values():
                if user.user_id == user_id:
                    user.telegram_chat_id = stored

    def get_active_telegram_recipient(self, user_id: str) -> str | None:
        """Return the authoritative active Telegram binding for SQL production paths."""
        if self._db():
            return self._repo.get_active_notification_recipient(user_id, "telegram")
        user = self.get_user(user_id)
        return user.telegram_chat_id if user is not None else None

    @staticmethod
    def _validate_password(password: str) -> str:
        if not 6 <= len(password) <= 128:
            raise ValueError("密码长度必须为 6 到 128 位")
        return password

    def seed_admin(self, username: str = "admin", password: str = "") -> None:
        if self._db():
            if self._repo.count_admins() == 0:
                self._validate_password(password)
                from zhice_os_v2.domain.security.password import hash_password
                self._repo.create(f"u-{secrets.token_hex(8)}", username, hash_password(password), "ADMIN")
        else:
            if username not in self._mem:
                self._mem[username] = (User(user_id="u-admin", username=username, role=Role.ADMIN), password)

    def authenticate(self, username: str, password: str = "") -> User | None:
        if self._db():
            row = self._repo.get_by_username(username)
            if row is None or not row.is_active:
                return None
            from zhice_os_v2.domain.security.password import verify_password
            return self._row(row) if verify_password(password, row.password_hash) else None
        entry = self._mem.get(username)
        if entry is None:
            return None
        user, stored_pw = entry
        from zhice_os_v2.config.settings import get_settings
        required = get_settings().admin_password or stored_pw
        return user if not required or password == required else None

    def get_user(self, user_id: str) -> User | None:
        if self._db():
            row = self._repo.get_by_id(user_id)
            return self._row(row) if row else None
        for user, _ in self._mem.values():
            if user.user_id == user_id:
                return user
        return None

    def list_users(self) -> list[User]:
        if self._db():
            return [self._row(r) for r in self._repo.list_all()]
        return [u for u, _ in self._mem.values()]

    def create_user(self, username: str, password: str, role: str) -> User:
        if not self._db():
            raise RuntimeError("create_user 仅在 DB 模式下可用")
        validated_role = Role(role).value
        self._validate_password(password)
        if self._repo.get_by_username(username) is not None:
            # D10：code 化（与 register 同键），端点透传 {code} 供前端 i18n 翻译
            raise ValueError("auth.username_taken")
        from zhice_os_v2.domain.security.password import hash_password
        return self._row(
            self._repo.create(
                f"u-{secrets.token_hex(8)}",
                username,
                hash_password(password),
                validated_role,
            )
        )

    def register(self, username: str, email: str, password: str) -> User:
        """公开注册：**强制 USER 角色**(忽略任何客户端角色输入，防提权)。

        校验 用户名非空/邮箱格式/密码长度/用户名唯一/邮箱唯一。
        失败抛 ValueError，其消息即前端 errors 命名空间的 code(端点直接透传)。
        """
        if not self._db():
            raise RuntimeError("register 仅在 DB 模式下可用")
        username = (username or "").strip()
        if not username:
            raise ValueError("auth.username_empty")
        try:
            # RFC 严格校验 + 规范化；check_deliverability=False 不查 DNS(快/离线可用)
            email = validate_email((email or "").strip(), check_deliverability=False).normalized.lower()
        except EmailNotValidError:
            raise ValueError("auth.invalid_email")
        if not 6 <= len(password) <= 128:
            raise ValueError("auth.password_too_short")
        if self._repo.get_by_username(username) is not None:
            raise ValueError("auth.username_taken")
        if self._repo.get_by_email(email) is not None:
            raise ValueError("auth.email_taken")
        from zhice_os_v2.domain.security.password import hash_password
        return self._row(
            self._repo.create(
                f"u-{secrets.token_hex(8)}",
                username,
                hash_password(password),
                Role.USER.value,  # 硬编码，公开注册一律 owner-scoped USER，杜绝提权
                email=email,
            )
        )

    def update_role_by_username(
        self, username: str, role: str, expected_auth_version: int | None = None
    ) -> User:
        if not self._db():
            raise RuntimeError("update_role 仅在 DB 模式下可用")
        row = self._repo.get_by_username(username)
        if row is None:
            # D10：code 化且不回显入参
            raise ValueError("auth.user_not_found")
        return self.update_role_by_id(row.user_id, role, expected_auth_version)

    def update_role_by_id(
        self, user_id: str, role: str, expected_auth_version: int | None = None
    ) -> User:
        if not self._db():
            raise RuntimeError("update_role 仅在 DB 模式下可用")
        # Role(role) 先行校验非法角色（如 ROOT）——在任何写之前抛，不脏库。
        validated_role = Role(role).value
        row = self._repo.update_role_protected(
            user_id, validated_role, expected_auth_version
        )
        return self._row(row)

    def change_password(
        self, user_id: str, new_password: str,
        old_password: str | None = None, *, verify_old: bool = False,
    ) -> None:
        """改密（3b）。

        verify_old=True（自助改密）：old_password 必填且必须验对当前密码哈希，
        否则 ValueError（auth.old_password_required / auth.wrong_old_password）——
        消息即前端 errors code。verify_old=False（ADMIN 管理重置他人）：不验旧密。
        越权判定（self-or-ADMIN）在 API 端点层，服务层只管密码学正确性。
        """
        if not self._db():
            raise RuntimeError("change_password 仅在 DB 模式下可用")
        self._validate_password(new_password)
        row = self._repo.get_by_id(user_id)
        if row is None:
            raise ValueError("auth.user_not_found")
        if verify_old:
            if not old_password:
                raise ValueError("auth.old_password_required")
            from zhice_os_v2.domain.security.password import verify_password
            if not verify_password(old_password, row.password_hash):
                raise ValueError("auth.wrong_old_password")
        from zhice_os_v2.domain.security.password import hash_password
        self._repo.update_password(user_id, hash_password(new_password))

    def deactivate_user(
        self, user_id: str, expected_auth_version: int | None = None
    ) -> User:
        if not self._db():
            raise RuntimeError("deactivate_user 仅在 DB 模式下可用")
        row = self._repo.deactivate_protected(user_id, expected_auth_version)
        return self._row(row)
