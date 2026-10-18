"""Telegram 定向通知配置与模式组合校验测试（Phase C Task 1：仅配置与校验，无消费方）。"""
from __future__ import annotations

from typing import Any

import pytest

from zhice_os_v2.config.settings import AppSettings

# 屏蔽进程 env 与 .env.local 的干扰（本机 .env.local 设有 TELEGRAM_BOT_TOKEN/TELEGRAM_PROXY）。
_ISOLATED_ENV_VARS = (
    "APP_ENV",
    "MULTI_USER_EXECUTION_MODE",
    "TELEGRAM_NOTIFICATION_MODE",
    "TELEGRAM_BINDING_ENABLED",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_BOT_USERNAME",
    "TELEGRAM_WEBHOOK_SECRET",
    "TELEGRAM_WEBHOOK_PUBLIC_URL",
)


def _settings(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> AppSettings:
    """隔离构造：清掉相关 env 并禁读 .env.local，仅用显式参数。"""
    for var in _ISOLATED_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return AppSettings(_env_file=None, **kwargs)


def test_directed_mode_requires_enforced_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """directed 通知必须依赖 enforced 归属执行；legacy/shadow 组合启动即拒绝。"""
    with pytest.raises(
        ValueError, match="directed notifications require enforced ownership"
    ):
        _settings(
            monkeypatch,
            multi_user_execution_mode="legacy",
            telegram_notification_mode="directed",
        )
    with pytest.raises(
        ValueError, match="directed notifications require enforced ownership"
    ):
        _settings(
            monkeypatch,
            multi_user_execution_mode="shadow",
            telegram_notification_mode="directed",
        )
    # enforced + directed 为唯一合法组合
    ok = _settings(
        monkeypatch,
        multi_user_execution_mode="enforced",
        telegram_notification_mode="directed",
    )
    assert ok.telegram_notification_mode == "directed"


def test_binding_config_requires_bot_username_and_webhook_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """开启绑定必须齐备 bot token/username/webhook secret，且公网 URL 为 https://。"""
    with pytest.raises(ValueError, match="telegram binding config"):
        _settings(monkeypatch, telegram_binding_enabled=True, telegram_bot_token="token")
    # http:// 公网 URL 拒绝（本机/159 HTTP、113 自签名均不满足）
    with pytest.raises(ValueError, match="telegram binding config"):
        _settings(
            monkeypatch,
            telegram_binding_enabled=True,
            telegram_bot_token="token",
            telegram_bot_username="zhice_bot",
            telegram_webhook_secret="secret",
            telegram_webhook_public_url="http://example.com/webhook",
        )
    # 四项齐备且 https:// 通过
    ok = _settings(
        monkeypatch,
        telegram_binding_enabled=True,
        telegram_bot_token="token",
        telegram_bot_username="zhice_bot",
        telegram_webhook_secret="secret",
        telegram_webhook_public_url="https://example.com/webhook",
    )
    assert ok.telegram_binding_enabled is True


def test_default_notification_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认保持广播行为、绑定关闭（安全铁律：不改变现状）。"""
    settings = _settings(monkeypatch)
    assert settings.telegram_notification_mode == "broadcast"
    assert settings.telegram_binding_enabled is False
    assert settings.telegram_challenge_ttl_seconds == 600
    assert settings.notification_worker_batch_size == 50
    assert settings.notification_retry_max == 6


# ══════════════════════════════════════════════════════════════════════════════
# Phase C Task 3：一次性 deep-link challenge 与 webhook
#
# 安全属性覆盖：
# - DB 只存 token 的 SHA-256 哈希，token 原文不落库、不进日志；
# - 新 challenge 作废旧未消费 challenge；过期/已消费/未知 token → expired_or_used；
# - chat_id 已被其他用户绑定 → chat_conflict（不覆盖他人绑定）；
# - webhook：secret 缺失/错误/未配置或 binding 关闭 → 403（fail-closed）；
#   非 private-chat / 非 /start 文本 / 其他 update 形态 → 200 忽略；
#   一切消费结果均 200（不向 Telegram 泄露内部状态）。
# ══════════════════════════════════════════════════════════════════════════════

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.base import Base

_WEBHOOK_SECRET = "whsec-test"

_ENABLED_KWARGS: dict[str, Any] = {
    "telegram_binding_enabled": True,
    "telegram_bot_token": "token",
    "telegram_bot_username": "test_bot",
    "telegram_webhook_secret": _WEBHOOK_SECRET,
    "telegram_webhook_public_url": "https://example.com/hook",
}


@pytest.fixture
def binding_env(monkeypatch: pytest.MonkeyPatch):
    """隔离 sqlite（challenge+user 全表）+ 绑定开启 settings（bot=test_bot）。

    - 仓储/服务两模块的 get_session / get_settings 均指向隔离实现；
    - 返回 SimpleNamespace(Session=会话工厂, settings=已开启绑定的 AppSettings)。
    """
    import zhice_os_v2.application.services.telegram_binding_service as svc_mod
    import zhice_os_v2.infrastructure.repositories.telegram_binding_repository as repo_mod

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(repo_mod, "get_session", lambda: Session())
    settings = _settings(monkeypatch, **_ENABLED_KWARGS)
    monkeypatch.setattr(svc_mod, "get_settings", lambda: settings)
    return SimpleNamespace(Session=Session, settings=settings)


def _seed_user(env, user_id: str, chat_id: str | None = None) -> None:
    """向隔离库插入一个 active 用户（consume 绑定 chat_id 的目标行）。"""
    s = env.Session()
    try:
        s.add(
            orm.UserModel(
                user_id=user_id,
                username=user_id,
                password_hash="x",
                role="TRADER",  # ck_v2_user_role 不含 USER；TRADER 是合法枚举
                is_active=True,
                locale="zh",
                telegram_chat_id=chat_id,
                created_at=datetime.now(UTC),
            )
        )
        s.commit()
    finally:
        s.close()


def _svc():
    from zhice_os_v2.application.services.telegram_binding_service import (
        TelegramBindingService,
    )

    return TelegramBindingService()


def _user_chat_id(env, user_id: str) -> str | None:
    s = env.Session()
    try:
        row = s.query(orm.UserModel).filter_by(user_id=user_id).one()
        return row.telegram_chat_id
    finally:
        s.close()


@pytest.fixture
def binding_service(binding_env, users):
    _seed_user(binding_env, users.alice.user_id)
    _seed_user(binding_env, users.bob.user_id)
    return _svc()


# ── challenge 生命周期 ────────────────────────────────────────────────────────

def test_create_challenge_stores_hash_only(binding_service, users) -> None:
    result = binding_service.create_challenge(users.alice)
    row = binding_service.repo.latest_for_user(users.alice.user_id)
    assert result.url.startswith("https://t.me/test_bot?start=")
    assert result.token not in row.token_hash
    assert row.token_hash == hashlib.sha256(result.token.encode()).hexdigest()
    # token 原文绝不落库：challenge 任何列都不含 token
    for col in ("challenge_id", "token_hash"):
        assert result.token not in getattr(row, col)
    # TTL 生效（默认 600s 窗口内）
    assert datetime.now(UTC) < result.expires_at
    assert result.expires_at <= datetime.now(UTC) + timedelta(seconds=600)


def test_create_challenge_rejects_when_disabled(
    binding_env, monkeypatch: pytest.MonkeyPatch, users
) -> None:
    """fail-closed：binding 未开启时创建 challenge 直接拒绝，且不产生任何行。"""
    import zhice_os_v2.application.services.telegram_binding_service as svc_mod

    disabled = _settings(monkeypatch)
    monkeypatch.setattr(svc_mod, "get_settings", lambda: disabled)
    with pytest.raises(ValueError, match="telegram.binding_disabled"):
        _svc().create_challenge(users.alice)
    s = binding_env.Session()
    try:
        assert s.query(orm.TelegramBindingChallengeModel).count() == 0
    finally:
        s.close()


def test_new_challenge_invalidates_old(binding_service, users) -> None:
    first = binding_service.create_challenge(users.alice)
    second = binding_service.create_challenge(users.alice)
    assert binding_service.consume(first.token, "123") == "expired_or_used"
    assert binding_service.consume(second.token, "123") == "bound"


def test_expired_challenge_rejected(binding_service, binding_env, users) -> None:
    challenge = binding_service.create_challenge(users.alice)
    s = binding_env.Session()
    try:
        row = (
            s.query(orm.NotificationBindingChallengeModel)
            .filter_by(challenge_id=challenge.challenge_id)
            .one()
        )
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        s.commit()
    finally:
        s.close()
    assert binding_service.consume(challenge.token, "123") == "expired_or_used"


def test_consume_unknown_token(binding_service) -> None:
    assert binding_service.consume("no-such-token", "123") == "expired_or_used"


def test_consume_binds_chat_id(binding_service, binding_env, users) -> None:
    challenge = binding_service.create_challenge(users.alice)
    assert binding_service.consume(challenge.token, "123456") == "bound"
    assert _user_chat_id(binding_env, users.alice.user_id) == "123456"


def test_consume_replay_keeps_original_binding(
    binding_service, binding_env, users
) -> None:
    """已消费 token 重放 → expired_or_used，绝不改已落库的绑定。"""
    challenge = binding_service.create_challenge(users.alice)
    assert binding_service.consume(challenge.token, "111") == "bound"
    assert binding_service.consume(challenge.token, "222") == "expired_or_used"
    assert _user_chat_id(binding_env, users.alice.user_id) == "111"


def test_consume_chat_conflict_keeps_other_user_binding(
    binding_env, users
) -> None:
    """chat_id 已被其他用户绑定 → chat_conflict，不覆盖他人绑定。"""
    _seed_user(binding_env, users.alice.user_id)
    _seed_user(binding_env, users.bob.user_id, chat_id="777")
    svc = _svc()
    challenge = svc.create_challenge(users.alice)
    assert svc.consume(challenge.token, "777") == "chat_conflict"
    assert _user_chat_id(binding_env, users.bob.user_id) == "777"
    assert _user_chat_id(binding_env, users.alice.user_id) is None


def test_no_token_plaintext_in_logs(binding_service, users, caplog) -> None:
    """token 原文绝不进日志（challenge 创建 + 消费全链路）。"""
    with caplog.at_level(logging.DEBUG):
        challenge = binding_service.create_challenge(users.alice)
        binding_service.consume(challenge.token, "555")
    assert challenge.token not in caplog.text


# ── webhook ───────────────────────────────────────────────────────────────────


def _tg_update(
    *, chat_id: int = 555, chat_type: str = "private", text: str = "/start TOKEN"
) -> dict[str, Any]:
    return {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "chat": {"id": chat_id, "type": chat_type},
            "text": text,
        },
    }


_GOOD_SECRET_HEADER = {"X-Telegram-Bot-Api-Secret-Token": _WEBHOOK_SECRET}


@pytest.fixture
def webhook_client(binding_env, monkeypatch: pytest.MonkeyPatch, users):
    """挂好 webhook 路由的 TestClient：settings 指向隔离配置，send_telegram 打桩。"""
    import zhice_os_v2.api.routers.integrations_telegram as wh_mod

    _seed_user(binding_env, users.alice.user_id)
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(wh_mod, "get_settings", lambda: binding_env.settings)
    monkeypatch.setattr(
        wh_mod, "send_telegram", lambda chat_id, text: sent.append((chat_id, text)) or True
    )
    from zhice_os_v2.api.main import app

    return SimpleNamespace(client=TestClient(app), sent=sent)


def test_webhook_rejects_missing_secret(webhook_client) -> None:
    resp = webhook_client.client.post(
        "/api/v1/integrations/telegram/webhook", json=_tg_update()
    )
    assert resp.status_code == 403


def test_webhook_rejects_wrong_secret(webhook_client) -> None:
    resp = webhook_client.client.post(
        "/api/v1/integrations/telegram/webhook",
        json=_tg_update(),
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )
    assert resp.status_code == 403


def test_webhook_rejects_when_binding_disabled(
    binding_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fail-closed：binding 关闭（secret 必然未配齐）时 webhook 一律 403。"""
    import zhice_os_v2.api.routers.integrations_telegram as wh_mod

    disabled = _settings(monkeypatch)
    monkeypatch.setattr(wh_mod, "get_settings", lambda: disabled)
    from zhice_os_v2.api.main import app

    client = TestClient(app)
    resp = client.post(
        "/api/v1/integrations/telegram/webhook",
        json=_tg_update(),
        headers=_GOOD_SECRET_HEADER,
    )
    assert resp.status_code == 403


def test_webhook_ignores_non_start_text(webhook_client, binding_env, users) -> None:
    """非 /start 文本 → 200 忽略，且不消费仍有效的 challenge。"""
    svc = _svc()
    challenge = svc.create_challenge(users.alice)
    resp = webhook_client.client.post(
        "/api/v1/integrations/telegram/webhook",
        json=_tg_update(text="hello world"),
        headers=_GOOD_SECRET_HEADER,
    )
    assert resp.status_code == 200
    assert _user_chat_id(binding_env, users.alice.user_id) is None
    # challenge 未被消费：服务层仍可正常绑定
    assert svc.consume(challenge.token, "555") == "bound"


def test_webhook_ignores_group_chat(webhook_client, binding_env, users) -> None:
    """非 private chat（群组）→ 200 忽略，不消费 challenge。"""
    svc = _svc()
    challenge = svc.create_challenge(users.alice)
    resp = webhook_client.client.post(
        "/api/v1/integrations/telegram/webhook",
        json=_tg_update(chat_id=-100200, chat_type="group"),
        headers=_GOOD_SECRET_HEADER,
    )
    assert resp.status_code == 200
    assert _user_chat_id(binding_env, users.alice.user_id) is None
    assert svc.consume(challenge.token, "555") == "bound"


def test_webhook_ignores_other_update_shapes(webhook_client) -> None:
    """channel_post/edited_message 等其他 update 形态 → 200 忽略。"""
    for body in (
        {"update_id": 2, "channel_post": {"chat": {"id": -100, "type": "channel"}, "text": "/start x"}},
        {"update_id": 3, "edited_message": {"chat": {"id": 555, "type": "private"}, "text": "/start x"}},
        {"update_id": 4},
        {},
    ):
        resp = webhook_client.client.post(
            "/api/v1/integrations/telegram/webhook", json=body, headers=_GOOD_SECRET_HEADER
        )
        assert resp.status_code == 200


def test_webhook_binds_valid_token(webhook_client, binding_env, users) -> None:
    """合法 token：200 + 用户 chat_id 落库 + 确认消息（不含 token）。"""
    challenge = _svc().create_challenge(users.alice)
    resp = webhook_client.client.post(
        "/api/v1/integrations/telegram/webhook",
        json=_tg_update(chat_id=555, text=f"/start {challenge.token}"),
        headers=_GOOD_SECRET_HEADER,
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert _user_chat_id(binding_env, users.alice.user_id) == "555"
    assert len(webhook_client.sent) == 1
    confirm_chat, confirm_text = webhook_client.sent[0]
    assert confirm_chat == "555"
    assert challenge.token not in confirm_text


def test_webhook_replay_returns_200_without_rebinding(
    webhook_client, binding_env, users
) -> None:
    challenge = _svc().create_challenge(users.alice)
    post = webhook_client.client.post
    first = post(
        "/api/v1/integrations/telegram/webhook",
        json=_tg_update(chat_id=555, text=f"/start {challenge.token}"),
        headers=_GOOD_SECRET_HEADER,
    )
    second = post(
        "/api/v1/integrations/telegram/webhook",
        json=_tg_update(chat_id=666, text=f"/start {challenge.token}"),
        headers=_GOOD_SECRET_HEADER,
    )
    assert first.status_code == second.status_code == 200
    # 重放不改变绑定，也不再发确认消息
    assert _user_chat_id(binding_env, users.alice.user_id) == "555"
    assert len(webhook_client.sent) == 1


# ── /auth/me/telegram 端点 ────────────────────────────────────────────────────


@pytest.fixture
def auth_client(binding_env, monkeypatch: pytest.MonkeyPatch, auth_headers):
    """auth 端点 TestClient：settings 指向隔离配置（binding 开启）。"""
    import zhice_os_v2.api.routers.auth as auth_mod

    monkeypatch.setattr(auth_mod, "get_settings", lambda: binding_env.settings)
    from zhice_os_v2.api.main import app

    return SimpleNamespace(client=TestClient(app), headers=auth_headers)


def test_challenge_endpoint_returns_url_once(auth_client, binding_env) -> None:
    _seed_user(binding_env, "u-admin")
    resp = auth_client.client.post(
        "/api/v1/auth/me/telegram/challenge", headers=auth_client.headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["url"].startswith("https://t.me/test_bot?start=")
    assert set(body) == {"url", "expires_at"}
    assert body["expires_at"]


def test_challenge_endpoint_requires_auth(auth_client) -> None:
    resp = auth_client.client.post("/api/v1/auth/me/telegram/challenge")
    assert resp.status_code == 401


def test_challenge_endpoint_disabled_rejected(
    binding_env, monkeypatch: pytest.MonkeyPatch, auth_headers
) -> None:
    import zhice_os_v2.api.routers.auth as auth_mod
    import zhice_os_v2.application.services.telegram_binding_service as svc_mod

    disabled = _settings(monkeypatch)
    monkeypatch.setattr(auth_mod, "get_settings", lambda: disabled)
    monkeypatch.setattr(svc_mod, "get_settings", lambda: disabled)
    from zhice_os_v2.api.main import app

    client = TestClient(app)
    resp = client.post("/api/v1/auth/me/telegram/challenge", headers=auth_headers)
    assert resp.status_code == 409
    assert resp.json()["detail"] == {"code": "telegram.binding_disabled"}


def test_telegram_status_masks_chat_id(
    binding_env, monkeypatch: pytest.MonkeyPatch, users
) -> None:
    import zhice_os_v2.api.routers.auth as auth_mod

    monkeypatch.setattr(auth_mod, "get_settings", lambda: binding_env.settings)
    from zhice_os_v2.api.dependencies import get_current_user
    from zhice_os_v2.api.main import app
    from zhice_os_v2.domain.security.user import Role, User

    _seed_user(binding_env, users.alice.user_id, chat_id="12345678")
    _seed_binding = orm.UserNotificationBindingModel(
        binding_id="binding-status-alice",
        user_id=users.alice.user_id,
        channel="telegram",
        recipient_id="12345678",
        status="active",
        bound_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    with binding_env.Session() as session:
        session.add(_seed_binding)
        session.commit()
    app.dependency_overrides[get_current_user] = lambda: User(
        user_id=users.alice.user_id,
        username=users.alice.username,
        role=Role.USER,
        telegram_chat_id="12345678",
    )
    from zhice_os_v2.api.routers.auth import get_auth_service
    from zhice_os_v2.application.services.auth_service import AuthService
    from zhice_os_v2.infrastructure.repositories.user_repository import UserRepository

    app.dependency_overrides[get_auth_service] = lambda: AuthService(repo=UserRepository())
    import zhice_os_v2.infrastructure.repositories.user_repository as user_repo_mod
    monkeypatch.setattr(user_repo_mod, "get_session", lambda: binding_env.Session())
    try:
        client = TestClient(app)
        resp = client.get("/api/v1/auth/me/telegram/status")
        assert resp.status_code == 200
        assert resp.json() == {
            "bound": True,
            "chat_id": "****5678",
            "bot_username": "test_bot",
        }
    finally:
        app.dependency_overrides.clear()


def test_telegram_status_unbound(
    binding_env, monkeypatch: pytest.MonkeyPatch, users
) -> None:
    import zhice_os_v2.api.routers.auth as auth_mod

    monkeypatch.setattr(auth_mod, "get_settings", lambda: binding_env.settings)
    from zhice_os_v2.api.dependencies import get_current_user
    from zhice_os_v2.api.main import app
    from zhice_os_v2.domain.security.user import Role, User

    app.dependency_overrides[get_current_user] = lambda: User(
        user_id=users.alice.user_id,
        username=users.alice.username,
        role=Role.USER,
        telegram_chat_id=None,
    )
    try:
        client = TestClient(app)
        resp = client.get("/api/v1/auth/me/telegram/status")
        assert resp.status_code == 200
        assert resp.json() == {"bound": False, "chat_id": None, "bot_username": "test_bot"}
    finally:
        app.dependency_overrides.clear()


def test_delete_telegram_unbinds(
    binding_env, monkeypatch: pytest.MonkeyPatch, users
) -> None:
    """DELETE /auth/me/telegram → 解绑（chat_id 置 None）。"""
    from zhice_os_v2.api.dependencies import get_current_user
    from zhice_os_v2.api.main import app
    from zhice_os_v2.api.routers.auth import get_auth_service
    from zhice_os_v2.domain.security.user import Role, User

    recorded: list[tuple[str, str]] = []

    class _StubAuthService:
        def set_telegram_chat_id(self, user_id: str, chat_id: str | None) -> None:
            recorded.append((user_id, chat_id or ""))

    app.dependency_overrides[get_current_user] = lambda: User(
        user_id=users.alice.user_id,
        username=users.alice.username,
        role=Role.USER,
    )
    app.dependency_overrides[get_auth_service] = lambda: _StubAuthService()
    try:
        client = TestClient(app)
        resp = client.delete("/api/v1/auth/me/telegram")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "telegram_chat_id": None}
        assert recorded == [(users.alice.user_id, "")]
    finally:
        app.dependency_overrides.clear()


def test_patch_manual_binding_rejected_when_enabled(auth_client) -> None:
    """binding 开启时 manual PATCH 拒绝，引导走 deep-link。"""
    resp = auth_client.client.patch(
        "/api/v1/auth/me/telegram",
        json={"chat_id": "999"},
        headers=auth_client.headers,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == {"code": "telegram.manual_binding_disabled"}


def test_patch_manual_binding_legacy_when_disabled(
    monkeypatch: pytest.MonkeyPatch, auth_headers
) -> None:
    """binding 关闭（未配 webhook 的部署）→ PATCH 保持旧行为。"""
    import zhice_os_v2.api.routers.auth as auth_mod

    disabled = _settings(monkeypatch)
    monkeypatch.setattr(auth_mod, "get_settings", lambda: disabled)
    from zhice_os_v2.api.main import app

    client = TestClient(app)
    resp = client.patch(
        "/api/v1/auth/me/telegram", json={"chat_id": "999"}, headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "telegram_chat_id": "***"}


def test_patch_manual_binding_chat_conflict_returns_409(
    monkeypatch: pytest.MonkeyPatch, users
) -> None:
    """终审 Important #2：0023 唯一索引下第二个用户 PATCH 同 chat_id → 409
    telegram.chat_conflict（对齐 webhook 路径语义），绝不 500、不覆盖他人绑定。"""
    import zhice_os_v2.api.routers.auth as auth_mod
    import zhice_os_v2.infrastructure.repositories.user_repository as user_repo_mod
    from zhice_os_v2.api.dependencies import get_current_user
    from zhice_os_v2.api.main import app
    from zhice_os_v2.api.routers.auth import get_auth_service
    from zhice_os_v2.application.services.auth_service import AuthService
    from zhice_os_v2.domain.security.user import Role, User
    from zhice_os_v2.infrastructure.repositories.user_repository import UserRepository

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(user_repo_mod, "get_session", lambda: Session())
    env = SimpleNamespace(Session=Session)
    _seed_user(env, users.alice.user_id)
    _seed_user(env, users.bob.user_id)

    # binding 关闭（默认部署）→ manual PATCH 路径活跃
    disabled = _settings(monkeypatch)
    monkeypatch.setattr(auth_mod, "get_settings", lambda: disabled)

    svc = AuthService(repo=UserRepository())

    def _as(user) -> User:
        return User(user_id=user.user_id, username=user.username, role=Role.USER)

    app.dependency_overrides[get_current_user] = lambda: _as(users.alice)
    app.dependency_overrides[get_auth_service] = lambda: svc
    try:
        client = TestClient(app)
        first = client.patch("/api/v1/auth/me/telegram", json={"chat_id": "777"})
        assert first.status_code == 200

        # 第二个用户绑同一 chat_id：撞 uq_v2_user_telegram_chat_id → 409（非 500）
        app.dependency_overrides[get_current_user] = lambda: _as(users.bob)
        conflict = client.patch("/api/v1/auth/me/telegram", json={"chat_id": "777"})
        assert conflict.status_code == 409
        assert conflict.json()["detail"] == {"code": "telegram.chat_conflict"}
        # 他人绑定不受影响（不静默改归属）
        assert _user_chat_id(env, users.alice.user_id) == "777"
        assert _user_chat_id(env, users.bob.user_id) is None

        # 不同 chat_id 正常绑定
        ok = client.patch("/api/v1/auth/me/telegram", json={"chat_id": "888"})
        assert ok.status_code == 200
        assert ok.json() == {"ok": True, "telegram_chat_id": "***"}
        assert _user_chat_id(env, users.bob.user_id) == "888"
    finally:
        app.dependency_overrides.clear()


def test_webhook_exempt_from_internal_key_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """终审 Important #3：internal_key_auth 豁免 webhook 路径。

    设 INTERNAL_API_KEY 的部署上 Telegram 服务器不带 X-Internal-Key——webhook
    必须可达（自有 secret 鉴权比内部键更强）；普通端点仍受内部键保护。"""
    import zhice_os_v2.api.main as main_mod
    import zhice_os_v2.api.routers.integrations_telegram as wh_mod
    from zhice_os_v2.api.main import app

    disabled = _settings(monkeypatch)  # binding 关闭 → webhook 自身 fail-closed 403
    monkeypatch.setattr(wh_mod, "get_settings", lambda: disabled)
    monkeypatch.setattr(
        main_mod,
        "get_settings",
        lambda: SimpleNamespace(internal_api_key="ik-secret"),
    )
    client = TestClient(app)
    resp = client.post("/api/v1/integrations/telegram/webhook", json=_tg_update())
    assert resp.status_code == 403  # 路由层 secret 拒绝，而非中间件 401
    assert resp.json() != {"error": "unauthorized"}
    # 普通端点无内部键仍被拦（中间件语义不变）
    other = client.post("/api/v1/auth/login", json={"username": "x", "password": "y"})
    assert other.status_code == 401
    assert other.json() == {"error": "unauthorized"}


def test_secret_ok_non_ascii_provided_does_not_raise():
    """复核Low：非ASCII字节的 provided 编码后比对返回 False（403），而非 TypeError→500。

    httpx 测试端发不出非 ASCII 头，但生产中恶意客户端可经 socket 发原始字节，
    Starlette 以 latin-1 解出非 ASCII str——str 版 compare_digest 会抛 TypeError。
    """
    from zhice_os_v2.api.routers.integrations_telegram import _secret_ok

    assert _secret_ok("秘密🔑", "s3cret") is False
    assert _secret_ok("s3cret", "s3cret") is True
    assert _secret_ok(None, "s3cret") is False
    assert _secret_ok("anything", "") is False  # 未配置 → fail-closed
