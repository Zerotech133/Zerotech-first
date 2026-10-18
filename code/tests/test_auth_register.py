"""公开注册 AuthService.register 单测。

重点锁死：默认 VIEWER(防提权)、用户名/邮箱唯一、邮箱格式、密码长度。
register 抛的 ValueError 消息即前端 errors code(端点直接透传)。
"""
from unittest.mock import MagicMock

import pytest

from zhice_os_v2.application.services.auth_service import AuthService
from zhice_os_v2.domain.security.user import Role


def _svc():
    repo = MagicMock()
    repo.get_by_username.return_value = None
    repo.get_by_email.return_value = None

    def _create(uid, username, pw_hash, role, email=None):
        m = MagicMock()
        m.user_id, m.username, m.role, m.is_active, m.locale, m.email = (
            uid, username, role, True, "zh", email,
        )
        return m

    repo.create.side_effect = _create
    return AuthService(repo=repo), repo


def test_register_defaults_user():
    svc, repo = _svc()
    u = svc.register("alice", "alice@example.com", "secret6")
    assert u.role == Role.USER
    # create 第 4 个位置参数(role)硬编码为 USER
    assert repo.create.call_args.args[3] == Role.USER.value


def test_register_no_privilege_escalation():
    """register 签名不接受 role，服务端一律 USER —— 结构性防提权。"""
    svc, repo = _svc()
    svc.register("bob", "bob@example.com", "secret6")
    assert repo.create.call_args.args[3] == "USER"


def test_register_signature_does_not_accept_role():
    """register 签名结构性不接受 role 参数，杜绝提权入口。"""
    svc, _ = _svc()
    with pytest.raises(TypeError):
        svc.register("alice", "a@example.com", "secret6", role="ADMIN")


def test_register_persists_email_lowercased():
    svc, repo = _svc()
    u = svc.register("carol", "Carol@Example.COM", "secret6")
    assert u.email == "carol@example.com"
    assert repo.create.call_args.kwargs["email"] == "carol@example.com"


def test_register_duplicate_username():
    svc, repo = _svc()
    repo.get_by_username.return_value = object()
    with pytest.raises(ValueError, match="auth.username_taken"):
        svc.register("alice", "a@example.com", "secret6")


def test_register_duplicate_email():
    svc, repo = _svc()
    repo.get_by_email.return_value = object()
    with pytest.raises(ValueError, match="auth.email_taken"):
        svc.register("alice", "a@example.com", "secret6")


def test_register_invalid_email():
    svc, _ = _svc()
    with pytest.raises(ValueError, match="auth.invalid_email"):
        svc.register("alice", "notanemail", "secret6")


def test_register_short_password():
    svc, _ = _svc()
    with pytest.raises(ValueError, match="auth.password_too_short"):
        svc.register("alice", "a@example.com", "123")


def test_register_empty_username():
    svc, _ = _svc()
    with pytest.raises(ValueError, match="auth.username_empty"):
        svc.register("   ", "a@example.com", "secret6")
