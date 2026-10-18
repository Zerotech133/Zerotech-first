"""用户语言偏好持久化回归锁：User.locale 默认 + AuthService.set_locale 校验。

方案A i18n：登录/GET me 返回 locale，前端切换语言时 PATCH /me 存 user.locale，登录跟随账户。
"""
from __future__ import annotations

from zhice_os_v2.application.services.auth_service import AuthService
from zhice_os_v2.domain.security.user import User


def test_user_default_locale_is_zh():
    assert User(user_id="u1", username="x").locale == "zh"


def test_set_locale_updates_user():
    svc = AuthService()  # 内存后端
    svc.seed_admin("admin", "pw123456")
    svc.set_locale("u-admin", "en")
    u = svc.get_user("u-admin")
    assert u is not None
    assert u.locale == "en"


def test_set_locale_rejects_invalid_falls_back_zh():
    svc = AuthService()
    svc.seed_admin("admin", "pw123456")
    svc.set_locale("u-admin", "fr")  # 非 zh/en → 回落 zh
    u = svc.get_user("u-admin")
    assert u is not None
    assert u.locale == "zh"
