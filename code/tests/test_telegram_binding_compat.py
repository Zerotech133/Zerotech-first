"""Telegram generic-binding compatibility and privacy contract."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.application.services.auth_service import AuthService
from zhice_os_v2.domain.security.password import hash_password
from zhice_os_v2.domain.security.user import Role, User
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.base import Base
from zhice_os_v2.infrastructure.repositories.user_repository import UserRepository


@pytest.fixture
def telegram_db(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    import zhice_os_v2.infrastructure.repositories.telegram_binding_repository as tg_repo
    import zhice_os_v2.infrastructure.repositories.user_repository as user_repo

    monkeypatch.setattr(tg_repo, "get_session", lambda: Session())
    monkeypatch.setattr(user_repo, "get_session", lambda: Session())
    return Session


def _seed_user(Session, user_id: str, *, role: str = "USER", old_chat: str | None = None):
    with Session() as session:
        session.add(
            orm.UserModel(
                user_id=user_id,
                username=user_id,
                password_hash=hash_password("pass123"),
                role=role,
                is_active=True,
                telegram_chat_id=old_chat,
                created_at=datetime.now(UTC),
            )
        )
        session.commit()


def _seed_binding(
    Session,
    user_id: str,
    recipient: str,
    *,
    status: str = "active",
):
    now = datetime.now(UTC)
    with Session() as session:
        session.add(
            orm.UserNotificationBindingModel(
                binding_id=f"binding-{user_id}",
                user_id=user_id,
                channel="telegram",
                recipient_id=recipient,
                status=status,
                bound_at=now,
                revoked_at=now if status == "revoked" else None,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()


def _active_binding(Session, user_id: str):
    with Session() as session:
        return (
            session.query(orm.UserNotificationBindingModel)
            .filter_by(user_id=user_id, channel="telegram", status="active")
            .one_or_none()
        )


def _old_chat(Session, user_id: str) -> str | None:
    with Session() as session:
        return session.query(orm.UserModel).filter_by(user_id=user_id).one().telegram_chat_id


def test_0033_backfilled_binding_is_authoritative_recipient(telegram_db) -> None:
    _seed_user(telegram_db, "u-backfill", old_chat="12345678")
    _seed_binding(telegram_db, "u-backfill", "12345678")

    repo = UserRepository()
    assert repo.get_active_notification_recipient("u-backfill", "telegram") == "12345678"
    assert repo.list_active_notification_recipients("telegram") == ["12345678"]


def test_manual_bind_updates_generic_and_legacy_atomically_and_masks_api(
    telegram_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_user(telegram_db, "u-manual")
    service = AuthService(repo=UserRepository())
    service.set_telegram_chat_id("u-manual", "12345678")

    assert _active_binding(telegram_db, "u-manual").recipient_id == "12345678"
    assert _old_chat(telegram_db, "u-manual") == "12345678"

    import zhice_os_v2.api.routers.auth as auth_router
    from zhice_os_v2.api.dependencies import get_current_user
    from zhice_os_v2.api.main import app
    from zhice_os_v2.api.routers.auth import get_auth_service

    monkeypatch.setattr(
        auth_router,
        "get_settings",
        lambda: SimpleNamespace(
            telegram_binding_enabled=False,
            telegram_bot_username="test_bot",
        ),
    )
    app.dependency_overrides[get_current_user] = lambda: User(
        "u-manual", "u-manual", Role.USER, telegram_chat_id="12345678"
    )
    app.dependency_overrides[get_auth_service] = lambda: service
    try:
        response = TestClient(app).patch(
            "/api/v1/auth/me/telegram", json={"chat_id": "12345678"}
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {"ok": True, "telegram_chat_id": "****5678"}
    assert "12345678" not in response.text


def test_manual_bind_conflict_rolls_back_generic_binding(telegram_db) -> None:
    _seed_user(telegram_db, "u-owner", old_chat="777777")
    _seed_binding(telegram_db, "u-owner", "777777")
    _seed_user(telegram_db, "u-target")
    service = AuthService(repo=UserRepository())

    with pytest.raises(ValueError, match="telegram.chat_conflict"):
        service.set_telegram_chat_id("u-target", "777777")

    assert _active_binding(telegram_db, "u-target") is None
    assert _old_chat(telegram_db, "u-target") is None


def test_deep_link_uses_generic_challenge_and_syncs_both_then_replay_is_safe(
    telegram_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zhice_os_v2.application.services.telegram_binding_service as service_module
    from zhice_os_v2.application.services.telegram_binding_service import TelegramBindingService

    _seed_user(telegram_db, "u-deep")
    monkeypatch.setattr(
        service_module,
        "get_settings",
        lambda: SimpleNamespace(
            telegram_binding_enabled=True,
            telegram_challenge_ttl_seconds=600,
            telegram_bot_username="test_bot",
        ),
    )
    service = TelegramBindingService()
    challenge = service.create_challenge(User("u-deep", "u-deep", Role.USER))

    with telegram_db() as session:
        assert session.query(orm.NotificationBindingChallengeModel).count() == 1
        assert session.query(orm.TelegramBindingChallengeModel).count() == 0

    assert service.consume(challenge.token, "456789") == "bound"
    assert _active_binding(telegram_db, "u-deep").recipient_id == "456789"
    assert _old_chat(telegram_db, "u-deep") == "456789"
    assert service.consume(challenge.token, "999999") == "expired_or_used"
    assert _active_binding(telegram_db, "u-deep").recipient_id == "456789"
    assert _old_chat(telegram_db, "u-deep") == "456789"


def test_fresh_challenge_for_same_active_recipient_returns_bound_then_replay_expires(
    telegram_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zhice_os_v2.application.services.telegram_binding_service as service_module
    from zhice_os_v2.application.services.telegram_binding_service import TelegramBindingService

    _seed_user(telegram_db, "u-rebind", old_chat="456789")
    _seed_binding(telegram_db, "u-rebind", "456789")
    monkeypatch.setattr(
        service_module,
        "get_settings",
        lambda: SimpleNamespace(
            telegram_binding_enabled=True,
            telegram_challenge_ttl_seconds=600,
            telegram_bot_username="test_bot",
        ),
    )
    service = TelegramBindingService()
    challenge = service.create_challenge(User("u-rebind", "u-rebind", Role.USER))

    assert service.consume(challenge.token, "456789") == "bound"
    assert service.consume(challenge.token, "456789") == "expired_or_used"


def test_webhook_confirms_fresh_same_recipient_rebind_once(
    telegram_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zhice_os_v2.api.routers.integrations_telegram as webhook_module
    import zhice_os_v2.application.services.telegram_binding_service as service_module
    from zhice_os_v2.api.main import app
    from zhice_os_v2.application.services.telegram_binding_service import TelegramBindingService

    _seed_user(telegram_db, "u-webhook-rebind", old_chat="555")
    _seed_binding(telegram_db, "u-webhook-rebind", "555")
    settings = SimpleNamespace(
        telegram_binding_enabled=True,
        telegram_challenge_ttl_seconds=600,
        telegram_bot_username="test_bot",
        telegram_webhook_secret="webhook-secret",
    )
    monkeypatch.setattr(service_module, "get_settings", lambda: settings)
    monkeypatch.setattr(webhook_module, "get_settings", lambda: settings)
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        webhook_module,
        "send_telegram",
        lambda recipient, text: sent.append((recipient, text)),
    )
    challenge = TelegramBindingService().create_challenge(
        User("u-webhook-rebind", "u-webhook-rebind", Role.USER)
    )
    body = {
        "update_id": 1,
        "message": {
            "chat": {"id": 555, "type": "private"},
            "text": f"/start {challenge.token}",
        },
    }
    headers = {"X-Telegram-Bot-Api-Secret-Token": "webhook-secret"}
    client = TestClient(app)

    first = client.post(
        "/api/v1/integrations/telegram/webhook", json=body, headers=headers
    )
    replay = client.post(
        "/api/v1/integrations/telegram/webhook", json=body, headers=headers
    )

    assert first.status_code == replay.status_code == 200
    assert sent == [("555", "✅ ZHICE·OS：Telegram 绑定成功，后续通知将发送到本会话。")]


def test_copied_legacy_challenge_can_be_consumed_by_generic_path(telegram_db) -> None:
    from hashlib import sha256

    from zhice_os_v2.application.services.telegram_binding_service import TelegramBindingService

    _seed_user(telegram_db, "u-copied")
    token = "copied-legacy-token"
    now = datetime.now(UTC)
    with telegram_db() as session:
        session.add(
            orm.NotificationBindingChallengeModel(
                challenge_id="nchallenge-copied",
                user_id="u-copied",
                channel="telegram",
                token_hash=sha256(token.encode()).hexdigest(),
                expires_at=now + timedelta(minutes=10),
                created_at=now,
            )
        )
        session.commit()

    assert TelegramBindingService().consume(token, "112233") == "bound"
    assert _active_binding(telegram_db, "u-copied").recipient_id == "112233"
    assert _old_chat(telegram_db, "u-copied") == "112233"


def test_unbind_revokes_binding_nulls_legacy_and_invalidates_challenge(telegram_db) -> None:
    from hashlib import sha256

    _seed_user(telegram_db, "u-unbind", old_chat="223344")
    _seed_binding(telegram_db, "u-unbind", "223344")
    now = datetime.now(UTC)
    with telegram_db() as session:
        session.add(
            orm.NotificationBindingChallengeModel(
                challenge_id="challenge-open",
                user_id="u-unbind",
                channel="telegram",
                token_hash=sha256(b"open-token").hexdigest(),
                expires_at=now + timedelta(minutes=10),
                created_at=now,
            )
        )
        session.commit()

    AuthService(repo=UserRepository()).set_telegram_chat_id("u-unbind", "")

    assert _active_binding(telegram_db, "u-unbind") is None
    assert _old_chat(telegram_db, "u-unbind") is None
    with telegram_db() as session:
        binding = session.query(orm.UserNotificationBindingModel).one()
        challenge = session.query(orm.NotificationBindingChallengeModel).one()
        assert binding.status == "revoked" and binding.revoked_at is not None
        assert challenge.consumed_at is not None


def test_revoked_binding_never_falls_back_to_stale_legacy_for_status_test_or_broadcast(
    telegram_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zhice_os_v2.api.routers.auth as auth_router
    from zhice_os_v2.api.dependencies import get_current_user
    from zhice_os_v2.api.main import app
    from zhice_os_v2.api.routers.auth import get_auth_service
    from zhice_os_v2.application.services import telegram_notifier

    _seed_user(telegram_db, "u-revoked", old_chat="99887766")
    _seed_binding(telegram_db, "u-revoked", "99887766", status="revoked")
    service = AuthService(repo=UserRepository())
    monkeypatch.setattr(
        auth_router,
        "get_settings",
        lambda: SimpleNamespace(
            telegram_binding_enabled=False,
            telegram_bot_username="test_bot",
        ),
    )
    app.dependency_overrides[get_current_user] = lambda: User(
        "u-revoked", "u-revoked", Role.USER, telegram_chat_id="99887766"
    )
    app.dependency_overrides[get_auth_service] = lambda: service
    sent: list[str] = []
    monkeypatch.setattr(
        telegram_notifier,
        "send_telegram",
        lambda recipient, text: sent.append(recipient)
        or telegram_notifier.TelegramSendResult(ok=True),
    )
    monkeypatch.setattr(telegram_notifier, "_bot_token", lambda: "token")
    import zhice_os_v2.config.settings as settings_module

    monkeypatch.setattr(
        settings_module,
        "get_settings",
        lambda: SimpleNamespace(persistence_backend="sql"),
    )
    try:
        client = TestClient(app)
        status = client.get("/api/v1/auth/me/telegram/status")
        test = client.post("/api/v1/auth/me/telegram/test")
        broadcast_count = telegram_notifier.broadcast_telegram("hello")
    finally:
        app.dependency_overrides.clear()

    assert status.json()["bound"] is False
    assert status.json()["chat_id"] is None
    assert test.status_code == 400
    assert broadcast_count == 0
    assert sent == []


def test_broadcast_and_admin_recipient_queries_use_active_users_bindings_and_dedupe(
    telegram_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    from zhice_os_v2.application.services import telegram_notifier

    _seed_user(telegram_db, "u-admin", role="ADMIN")
    _seed_user(telegram_db, "u-user", role="USER")
    _seed_user(telegram_db, "u-off", role="ADMIN")
    _seed_binding(telegram_db, "u-admin", "111111")
    _seed_binding(telegram_db, "u-user", "222222")
    _seed_binding(telegram_db, "u-off", "333333")
    with telegram_db() as session:
        session.query(orm.UserModel).filter_by(user_id="u-off").update({"is_active": False})
        session.commit()

    sent: list[str] = []
    monkeypatch.setattr(telegram_notifier, "_bot_token", lambda: "token")
    monkeypatch.setattr(
        telegram_notifier,
        "send_telegram",
        lambda recipient, text: sent.append(recipient)
        or telegram_notifier.TelegramSendResult(ok=True),
    )
    import zhice_os_v2.config.settings as settings_module

    monkeypatch.setattr(
        settings_module,
        "get_settings",
        lambda: SimpleNamespace(persistence_backend="sql"),
    )

    assert telegram_notifier.broadcast_telegram("all") == 2
    assert sent == ["111111", "222222"]
    sent.clear()
    assert telegram_notifier.notify_admins("admins") == 1
    assert sent == ["111111"]


@pytest.mark.parametrize(
    ("binding_status", "binding_recipient", "expected"),
    [
        ("revoked", "12345678", None),
        ("active", "87654321", "****4321"),
    ],
)
def test_sql_login_and_me_responses_use_authoritative_active_binding(
    telegram_db,
    binding_status: str,
    binding_recipient: str,
    expected: str | None,
) -> None:
    from zhice_os_v2.api.dependencies import get_current_user
    from zhice_os_v2.api.main import app
    from zhice_os_v2.api.routers.auth import get_auth_service

    user_id = f"u-auth-response-{binding_status}"
    _seed_user(telegram_db, user_id, old_chat="11223344")
    _seed_binding(
        telegram_db,
        user_id,
        binding_recipient,
        status=binding_status,
    )
    service = AuthService(repo=UserRepository())
    app.dependency_overrides[get_auth_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: User(
        user_id,
        user_id,
        Role.USER,
        telegram_chat_id="11223344",
    )
    try:
        client = TestClient(app)
        login = client.post(
            "/api/v1/auth/login",
            json={"username": user_id, "password": "pass123"},
        )
        me = client.get("/api/v1/auth/me")
    finally:
        app.dependency_overrides.clear()

    assert login.status_code == me.status_code == 200
    assert login.json()["telegram_chat_id"] == expected
    assert me.json()["telegram_chat_id"] == expected
    assert "11223344" not in login.text
    assert "11223344" not in me.text


def test_auth_responses_never_expose_full_telegram_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from zhice_os_v2.api.dependencies import get_current_user
    from zhice_os_v2.api.main import app
    from zhice_os_v2.api.routers.auth import get_auth_service

    secret_id = "12345678"
    user = User(
        "u-private",
        "private",
        Role.USER,
        email="private@example.com",
        telegram_chat_id=secret_id,
    )

    class StubAuthService:
        def authenticate(self, username, password):
            return user

        def register(self, username, email, password):
            return user

        def set_telegram_chat_id(self, user_id, chat_id):
            return None

        def get_active_telegram_recipient(self, user_id):
            return secret_id

    app.dependency_overrides[get_auth_service] = lambda: StubAuthService()
    app.dependency_overrides[get_current_user] = lambda: user
    import zhice_os_v2.api.routers.auth as auth_router

    monkeypatch.setattr(
        auth_router,
        "get_settings",
        lambda: SimpleNamespace(
            telegram_binding_enabled=False,
            telegram_bot_username="test_bot",
        ),
    )
    monkeypatch.setattr(
        "zhice_os_v2.application.services.membership_service.MembershipService.get_or_create_free",
        lambda self, user_id: None,
    )
    try:
        client = TestClient(app)
        responses = [
            client.post("/api/v1/auth/login", json={"username": "private", "password": "pass123"}),
            client.post(
                "/api/v1/auth/register",
                json={
                    "username": "private",
                    "email": "private@example.com",
                    "password": "pass123",
                },
            ),
            client.get("/api/v1/auth/me"),
            client.patch("/api/v1/auth/me/telegram", json={"chat_id": secret_id}),
        ]
    finally:
        app.dependency_overrides.clear()

    assert all(response.status_code < 300 for response in responses)
    assert all(secret_id not in response.text for response in responses)
    assert all(response.json().get("telegram_chat_id") == "****5678" for response in responses)


def test_short_recipient_is_fully_masked_in_telegram_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from zhice_os_v2.application.services import telegram_notifier

    monkeypatch.setattr(telegram_notifier, "_bot_token", lambda: "token")
    monkeypatch.setattr(telegram_notifier, "_proxy", lambda: None)
    import httpx

    def fail(*args, **kwargs):
        raise RuntimeError("network")

    monkeypatch.setattr(httpx, "post", fail)
    with caplog.at_level(logging.WARNING):
        telegram_notifier.send_telegram("1234", "hello")
    assert "1234" not in caplog.text
    assert "****" in caplog.text


def test_memory_backend_binding_behavior_is_preserved() -> None:
    service = AuthService(repo=None)
    service._mem["memory-user"] = (
        User("u-memory", "memory-user", Role.USER),
        "pass123",
    )
    service.set_telegram_chat_id("u-memory", "445566")
    assert service.get_user("u-memory").telegram_chat_id == "445566"
    service.set_telegram_chat_id("u-memory", "")
    assert service.get_user("u-memory").telegram_chat_id is None
