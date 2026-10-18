"""Task 5: channel-scoped notification preferences and self-service authorization."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.domain.security.user import Role, User

_URL = "/api/v1/my/notification-preferences"
_EVENT_FIELDS = (
    "signal_enabled",
    "fill_enabled",
    "morning_report_enabled",
    "evening_report_enabled",
)


class _EnabledInstances:
    def __init__(self, owners: list[str]) -> None:
        self._owners = owners

    def list_enabled_for_market(self, market: str):
        return [SimpleNamespace(user_id=user_id) for user_id in self._owners]


@pytest.fixture
def channel_env(monkeypatch: pytest.MonkeyPatch):
    import zhice_os_v2.infrastructure.repositories.notification_preference_repository as repo_mod
    from zhice_os_v2.api.dependencies import (
        get_current_user,
        get_notification_preference_service,
    )
    from zhice_os_v2.api.main import app
    from zhice_os_v2.application.services.notification_preference_service import (
        NotificationPreferenceService,
    )
    from zhice_os_v2.infrastructure.db.base import Base
    from zhice_os_v2.infrastructure.repositories.notification_preference_repository import (
        NotificationPreferenceRepository,
    )

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(repo_mod, "get_session", lambda: Session())

    repository = NotificationPreferenceRepository()
    service = NotificationPreferenceService(
        repository=repository,
        strategy_repository=_EnabledInstances([]),
    )
    app.dependency_overrides[get_notification_preference_service] = lambda: service

    def as_user(user: User) -> None:
        app.dependency_overrides[get_current_user] = lambda: user

    env = SimpleNamespace(
        Session=Session,
        repository=repository,
        service=service,
        client=TestClient(app),
        as_user=as_user,
        app=app,
    )
    try:
        yield env
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_notification_preference_service, None)


def _seed_user(env, user: User) -> None:
    from zhice_os_v2.infrastructure.db import models as orm

    with env.Session() as session:
        if session.query(orm.UserModel).filter_by(user_id=user.user_id).first() is None:
            session.add(
                orm.UserModel(
                    user_id=user.user_id,
                    username=user.username,
                    password_hash="x",
                    role=user.role.value,
                    is_active=True,
                    locale=user.locale,
                    created_at=datetime.now(UTC),
                )
            )
            session.commit()


def _set_binding(env, user: User, channel: str, *, status: str = "active") -> None:
    from zhice_os_v2.infrastructure.db import models as orm

    _seed_user(env, user)
    now = datetime.now(UTC)
    with env.Session() as session:
        row = (
            session.query(orm.UserNotificationBindingModel)
            .filter_by(user_id=user.user_id, channel=channel)
            .first()
        )
        if row is None:
            row = orm.UserNotificationBindingModel(
                binding_id=f"binding-{user.user_id}-{channel}",
                user_id=user.user_id,
                channel=channel,
                recipient_id=f"recipient-{user.user_id}-{channel}",
                status=status,
                bound_at=now,
                revoked_at=now if status == "revoked" else None,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
        else:
            row.status = status
            row.revoked_at = now if status == "revoked" else None
            row.updated_at = now
        session.commit()


def test_domain_and_repository_roundtrip_four_event_fields(channel_env, users) -> None:
    from zhice_os_v2.application.services.notification_preference_service import (
        NotificationPreferenceBody,
    )

    pref = channel_env.service.update(
        users.alice.user_id,
        NotificationPreferenceBody(
            signal_enabled=False,
            fill_enabled=False,
            morning_report_enabled=False,
            evening_report_enabled=False,
        ),
        channel="wxpusher",
    )
    assert tuple(getattr(pref, field) for field in _EVENT_FIELDS) == (
        False,
        False,
        False,
        False,
    )
    reread = channel_env.repository.get(users.alice.user_id, "wxpusher")
    assert reread is not None
    assert tuple(getattr(reread, field) for field in _EVENT_FIELDS) == (
        False,
        False,
        False,
        False,
    )


def test_preferences_are_channel_scoped(channel_env, users) -> None:
    from zhice_os_v2.application.services.notification_preference_service import (
        NotificationPreferenceBody,
    )

    _set_binding(channel_env, users.alice, "wxpusher")
    channel_env.service.update(
        users.alice.user_id,
        NotificationPreferenceBody(signal_enabled=False, fill_enabled=True),
        channel="wxpusher",
    )
    telegram = channel_env.service.get_or_create(users.alice.user_id, "telegram")
    wxpusher = channel_env.service.get_or_create(users.alice.user_id, "wxpusher")
    assert wxpusher.signal_enabled is False
    assert wxpusher.fill_enabled is True
    assert telegram.signal_enabled is True


@pytest.mark.parametrize("channel", ["email", "wechat", ""])
def test_unknown_channel_is_rejected_by_service(channel_env, users, channel: str) -> None:
    with pytest.raises(ValueError, match="notification.channel_invalid"):
        channel_env.service.get_or_create(users.alice.user_id, channel)


@pytest.mark.parametrize("field", _EVENT_FIELDS)
def test_four_event_switches_are_strict_bool(field: str) -> None:
    from zhice_os_v2.application.services.notification_preference_service import (
        NotificationPreferenceBody,
    )

    with pytest.raises(ValidationError):
        NotificationPreferenceBody.model_validate({field: "true"})


def test_unbound_channel_cannot_be_explicitly_enabled(channel_env, users) -> None:
    from zhice_os_v2.application.services.notification_preference_service import (
        NotificationPreferenceBody,
    )
    from zhice_os_v2.infrastructure.db import models as orm

    with pytest.raises(ValueError, match="notification.binding_required"):
        channel_env.service.update(
            users.alice.user_id,
            NotificationPreferenceBody(signal_enabled=True),
            channel="wxpusher",
        )

    with channel_env.Session() as session:
        assert (
            session.query(orm.UserNotificationPreferenceModel)
            .filter_by(user_id=users.alice.user_id, channel="wxpusher")
            .count()
            == 0
        )


def test_unbound_channel_can_be_disabled_or_update_non_event_fields(channel_env, users) -> None:
    from zhice_os_v2.application.services.notification_preference_service import (
        NotificationPreferenceBody,
    )

    pref = channel_env.service.update(
        users.alice.user_id,
        NotificationPreferenceBody(
            signal_enabled=False,
            market_reports_enabled={"US": True},
            timezone="UTC",
        ),
        channel="wxpusher",
    )
    assert pref.signal_enabled is False
    assert pref.market_reports_enabled == {"US": True}
    assert pref.timezone == "UTC"


def test_active_binding_allows_enable_and_revoked_binding_rejects(channel_env, users) -> None:
    from zhice_os_v2.application.services.notification_preference_service import (
        NotificationPreferenceBody,
    )

    channel_env.service.update(
        users.alice.user_id,
        NotificationPreferenceBody(fill_enabled=False),
        channel="wxpusher",
    )
    _set_binding(channel_env, users.alice, "wxpusher")
    enabled = channel_env.service.update(
        users.alice.user_id,
        NotificationPreferenceBody(fill_enabled=True),
        channel="wxpusher",
    )
    assert enabled.fill_enabled is True

    _set_binding(channel_env, users.alice, "wxpusher", status="revoked")
    channel_env.service.update(
        users.alice.user_id,
        NotificationPreferenceBody(fill_enabled=False),
        channel="wxpusher",
    )
    with pytest.raises(ValueError, match="notification.binding_required"):
        channel_env.service.update(
            users.alice.user_id,
            NotificationPreferenceBody(fill_enabled=True),
            channel="wxpusher",
        )


def test_channel_event_family_requires_binding_and_matching_switch(channel_env, users) -> None:
    from zhice_os_v2.application.services.notification_preference_service import (
        NotificationPreferenceBody,
    )

    _set_binding(channel_env, users.alice, "telegram")
    channel_env.service.update(
        users.alice.user_id,
        NotificationPreferenceBody(signal_enabled=False, fill_enabled=True),
    )
    assert channel_env.service.channel_event_enabled(
        users.alice.user_id, "telegram", "signal"
    ) is False
    assert channel_env.service.channel_event_enabled(
        users.alice.user_id, "telegram", "fill"
    ) is True
    assert channel_env.service.channel_event_enabled(
        users.alice.user_id, "telegram", "stop_loss"
    ) is True
    assert channel_env.service.channel_event_enabled(
        users.alice.user_id, "telegram", "take_profit"
    ) is True
    assert channel_env.service.channel_event_enabled(
        users.alice.user_id, "telegram", "order"
    ) is False

    _set_binding(channel_env, users.alice, "telegram", status="revoked")
    assert channel_env.service.channel_event_enabled(
        users.alice.user_id, "telegram", "fill"
    ) is False


def test_report_gate_requires_binding_market_and_report_kind(channel_env, users) -> None:
    from zhice_os_v2.application.services.notification_preference_service import (
        NotificationPreferenceBody,
    )

    channel_env.service.update(
        users.alice.user_id,
        NotificationPreferenceBody(
            market_reports_enabled={"US": True},
            morning_report_enabled=False,
        ),
        channel="wxpusher",
    )
    assert channel_env.service.report_enabled(
        users.alice.user_id, "wxpusher", "US", "evening"
    ) is False

    _set_binding(channel_env, users.alice, "wxpusher")
    assert channel_env.service.report_enabled(
        users.alice.user_id, "wxpusher", "US", "morning"
    ) is False
    assert channel_env.service.report_enabled(
        users.alice.user_id, "wxpusher", "US", "evening"
    ) is True
    assert channel_env.service.report_enabled(
        users.alice.user_id, "wxpusher", "CN", "evening"
    ) is False
    assert channel_env.service.report_enabled(
        users.alice.user_id, "wxpusher", "US", "weekly"
    ) is False
    assert channel_env.service.report_enabled(
        users.alice.user_id, "wxpusher", "JP", "evening"
    ) is False


def test_report_recipients_filter_binding_market_and_kind(channel_env, users) -> None:
    from zhice_os_v2.application.services.notification_preference_service import (
        NotificationPreferenceBody,
        NotificationPreferenceService,
    )

    _set_binding(channel_env, users.alice, "wxpusher")
    _set_binding(channel_env, users.bob, "wxpusher")
    service = NotificationPreferenceService(
        repository=channel_env.repository,
        strategy_repository=_EnabledInstances(
            [users.alice.user_id, users.bob.user_id, users.alice.user_id]
        ),
    )
    service.update(
        users.alice.user_id,
        NotificationPreferenceBody(
            market_reports_enabled={"US": True},
            morning_report_enabled=False,
        ),
        channel="wxpusher",
    )
    service.update(
        users.bob.user_id,
        NotificationPreferenceBody(
            market_reports_enabled={"US": True},
            evening_report_enabled=False,
        ),
        channel="wxpusher",
    )
    assert service.report_recipients("US", "wxpusher", "morning") == [
        users.bob.user_id
    ]
    assert service.report_recipients("US", "wxpusher", "evening") == [
        users.alice.user_id
    ]
    assert service.report_recipients("US", "wxpusher") == [
        users.alice.user_id,
        users.bob.user_id,
    ]


def test_api_channel_query_default_and_isolation(channel_env, users) -> None:
    _set_binding(channel_env, users.alice, "wxpusher")
    channel_env.as_user(users.alice)
    wx = channel_env.client.patch(
        f"{_URL}?channel=wxpusher", json={"signal_enabled": False}
    )
    assert wx.status_code == 200, wx.text
    assert wx.json()["channel"] == "wxpusher"
    assert wx.json()["signal_enabled"] is False

    telegram = channel_env.client.get(_URL)
    assert telegram.status_code == 200, telegram.text
    assert telegram.json()["channel"] == "telegram"
    assert telegram.json()["signal_enabled"] is True
    assert all(field in telegram.json() for field in _EVENT_FIELDS)


def test_api_explicit_null_is_noop(channel_env, users) -> None:
    channel_env.as_user(users.alice)
    seeded = channel_env.client.patch(
        _URL,
        json={"market_reports_enabled": {"US": True}, "timezone": "UTC"},
    )
    assert seeded.status_code == 200, seeded.text

    response = channel_env.client.patch(
        _URL,
        json={"market_reports_enabled": None, "timezone": None},
    )
    assert response.status_code == 200, response.text
    assert response.json()["market_reports_enabled"] == {"US": True}
    assert response.json()["timezone"] == "UTC"


def test_api_unknown_channel_and_binding_required(channel_env, users) -> None:
    channel_env.as_user(users.alice)
    invalid = channel_env.client.get(f"{_URL}?channel=email")
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "notification.channel_invalid"

    unbound = channel_env.client.patch(
        f"{_URL}?channel=wxpusher", json={"signal_enabled": True}
    )
    assert unbound.status_code == 409
    assert unbound.json()["detail"]["code"] == "notification.binding_required"


def test_viewer_can_patch_own_preferences_but_not_business_writes(channel_env) -> None:
    viewer = User("viewer-1", "viewer", Role.VIEWER)
    channel_env.as_user(viewer)
    own = channel_env.client.patch(
        f"{_URL}?channel=wxpusher", json={"signal_enabled": False}
    )
    assert own.status_code == 200, own.text
    assert own.json()["user_id"] == viewer.user_id

    business = channel_env.client.post("/api/v1/my/accounts", json={"name": "forbidden"})
    assert business.status_code == 403


def test_instance_notification_keys_remain_the_original_nine() -> None:
    from zhice_os_v2.domain.strategy.user_strategy import DEFAULT_NOTIFICATION_PREFERENCES

    assert set(DEFAULT_NOTIFICATION_PREFERENCES) == {
        "signal",
        "plan",
        "approval",
        "risk",
        "order",
        "fill",
        "stop_loss",
        "take_profit",
        "runtime_error",
    }
