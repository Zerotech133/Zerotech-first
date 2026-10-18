"""Channel-neutral notification binding repository transaction tests."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.base import Base

NOW = datetime(2026, 9, 17, 8, 0, tzinfo=UTC)


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'binding.sqlite3'}")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as seed:
        seed.add_all([_user("alice"), _user("bob"), _user("carol")])
        seed.commit()
    try:
        yield SimpleNamespace(engine=engine, Session=factory)
    finally:
        engine.dispose()


@pytest.fixture
def session(db) -> Iterator[Session]:
    value = db.Session()
    try:
        yield value
    finally:
        value.rollback()
        value.close()


@pytest.fixture(autouse=True)
def _fixed_repository_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    import zhice_os_v2.infrastructure.repositories.notification_binding_repository as repo_mod

    monkeypatch.setattr(repo_mod, "_utcnow", lambda: NOW)


@pytest.fixture
def repo():
    from zhice_os_v2.infrastructure.repositories.notification_binding_repository import (
        NotificationBindingRepository,
    )

    return NotificationBindingRepository()


def _user(user_id: str) -> orm.UserModel:
    return orm.UserModel(
        user_id=user_id,
        username=user_id,
        password_hash="x",
        role="TRADER",
        is_active=True,
        locale="zh",
        created_at=NOW,
    )


def _challenge(
    repo,
    session: Session,
    user_id: str,
    token: str,
    *,
    channel: str = "wxpusher",
    expires_at: datetime | None = None,
):
    return repo.create_challenge(
        session,
        user_id=user_id,
        channel=channel,
        token=token,
        expires_at=expires_at or NOW + timedelta(minutes=10),
    )


def _consume(
    repo,
    session: Session,
    token: str,
    recipient_id: str,
    *,
    channel: str = "wxpusher",
):
    return repo.consume_and_bind(
        session,
        token=token,
        channel=channel,
        recipient_id=recipient_id,
        now=NOW,
    )


def _bind(repo, session: Session, user_id: str, recipient_id: str, token: str):
    _challenge(repo, session, user_id, token)
    return _consume(repo, session, token, recipient_id)


def test_create_challenge_stores_only_sha256_hash(repo, session) -> None:
    token = "synthetic-high-entropy-token"
    row = _challenge(repo, session, "alice", token)
    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in row.token_hash
    assert token not in row.challenge_id
    assert all(token not in str(value) for value in vars(row).values())


def test_new_challenge_invalidates_previous_for_same_user_and_channel(repo, session) -> None:
    first = _challenge(repo, session, "alice", "token-one")
    second = _challenge(repo, session, "alice", "token-two")
    assert first.consumed_at is not None
    assert second.consumed_at is None
    assert _consume(repo, session, "token-one", "UID_A") == "expired_or_used"
    assert _consume(repo, session, "token-two", "UID_A") == "bound"


def test_new_challenge_does_not_invalidate_another_channel(repo, session) -> None:
    telegram = _challenge(repo, session, "alice", "telegram-token", channel="telegram")
    _challenge(repo, session, "alice", "wx-token")
    assert telegram.consumed_at is None


def test_set_provider_code_only_updates_current_unconsumed_challenge(repo, session) -> None:
    old = _challenge(repo, session, "alice", "old-token")
    current = _challenge(repo, session, "alice", "current-token")
    assert repo.set_provider_code(session, old.challenge_id, "OLD_CODE") is False
    assert repo.set_provider_code(session, current.challenge_id, "CURRENT_CODE") is True
    assert current.provider_code == "CURRENT_CODE"
    assert _consume(repo, session, "current-token", "UID_A") == "bound"
    assert repo.set_provider_code(session, current.challenge_id, "LATE_CODE") is False
    assert current.provider_code == "CURRENT_CODE"


def test_set_provider_code_rejects_expired_challenge(repo, session) -> None:
    expired = _challenge(
        repo,
        session,
        "alice",
        "expired-provider-token",
        expires_at=NOW - timedelta(seconds=1),
    )
    assert repo.set_provider_code(session, expired.challenge_id, "LATE_CODE") is False
    assert expired.provider_code is None


def test_set_provider_code_locks_user_before_relocking_challenge(
    repo, session, monkeypatch: pytest.MonkeyPatch
) -> None:
    challenge = _challenge(repo, session, "alice", "lock-order-token")
    events: list[str] = []
    original_lock_user = repo._lock_user

    def tracking_lock_user(candidate_session, user_id):
        events.append(f"user:{user_id}")
        return original_lock_user(candidate_session, user_id)

    monkeypatch.setattr(repo, "_lock_user", tracking_lock_user)
    from sqlalchemy.orm import Query

    original_with_for_update = Query.with_for_update

    def tracking_with_for_update(query, *args, **kwargs):
        descriptions = {item.get("name") for item in query.column_descriptions}
        if "NotificationBindingChallengeModel" in descriptions:
            events.append("challenge:for_update")
        return original_with_for_update(query, *args, **kwargs)

    monkeypatch.setattr(Query, "with_for_update", tracking_with_for_update)

    assert repo.set_provider_code(session, challenge.challenge_id, "LOCKED_CODE") is True
    assert events == ["user:alice", "challenge:for_update"]


def test_duplicate_provider_code_returns_false_without_poisoning_transaction(repo, session) -> None:
    first = _challenge(repo, session, "alice", "alice-token")
    second = _challenge(repo, session, "bob", "bob-token")
    assert repo.set_provider_code(session, first.challenge_id, "SHARED_CODE") is True

    assert repo.set_provider_code(session, second.challenge_id, "SHARED_CODE") is False
    assert session.is_active
    assert repo.set_provider_code(session, second.challenge_id, "BOB_CODE") is True


def test_consume_rejects_expired_token_and_replay(repo, session) -> None:
    _challenge(
        repo,
        session,
        "alice",
        "expired-token",
        expires_at=NOW - timedelta(seconds=1),
    )
    _challenge(repo, session, "bob", "fresh-token")
    assert _consume(repo, session, "expired-token", "UID_EXPIRED") == "expired_or_used"
    assert _consume(repo, session, "fresh-token", "UID_B") == "bound"
    assert _consume(repo, session, "fresh-token", "UID_B") == "expired_or_used"


def test_unknown_channels_are_rejected_by_every_channel_entrypoint(repo, session) -> None:
    calls: list[Callable[[], object]] = [
        lambda: repo.create_challenge(
            session,
            user_id="alice",
            channel="email",
            token="token",
            expires_at=NOW + timedelta(minutes=1),
        ),
        lambda: repo.consume_and_bind(
            session,
            token="token",
            channel="email",
            recipient_id="recipient",
            now=NOW,
        ),
        lambda: repo.bind_verified(
            session,
            user_id="alice",
            channel="email",
            recipient_id="recipient",
            now=NOW,
        ),
        lambda: repo.get_active(session, "alice", "email"),
        lambda: repo.revoke(session, user_id="alice", channel="email", now=NOW),
        lambda: repo.revoke_if_current(session, "alice", "email", "recipient", NOW),
    ]
    for call in calls:
        with pytest.raises(ValueError) as exc_info:
            call()
        assert exc_info.value.args == ("notification.unsupported_channel",)


def test_same_user_current_recipient_is_idempotent(repo, session) -> None:
    assert _bind(repo, session, "alice", "UID_A", "first-token") == "bound"
    original = repo.get_active(session, "alice", "wxpusher")
    assert original is not None
    original_id = original.binding_id
    original_bound_at = original.bound_at
    _challenge(repo, session, "alice", "second-token")
    assert _consume(repo, session, "second-token", "UID_A") == "already_bound"
    current = repo.get_active(session, "alice", "wxpusher")
    assert current is not None
    assert current.binding_id == original_id
    assert current.bound_at == original_bound_at
    assert _consume(repo, session, "second-token", "UID_A") == "expired_or_used"


@pytest.mark.parametrize("revoke_owner", [False, True])
def test_recipient_owned_by_another_user_conflicts_without_leaking_owner(
    repo, session, revoke_owner: bool
) -> None:
    _bind(repo, session, "alice", "UID_SHARED", "alice-token")
    if revoke_owner:
        assert repo.revoke(session, user_id="alice", channel="wxpusher", now=NOW)
    _challenge(repo, session, "bob", "bob-token")
    with pytest.raises(ValueError) as exc_info:
        _consume(repo, session, "bob-token", "UID_SHARED")
    assert exc_info.value.args == ("notification.binding_recipient_conflict",)
    assert "alice" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert repo.get_active(session, "bob", "wxpusher") is None


def test_rebind_updates_same_user_channel_row_to_new_recipient(repo, session) -> None:
    _bind(repo, session, "alice", "UID_OLD", "old-token")
    old = repo.get_active(session, "alice", "wxpusher")
    assert old is not None
    binding_id = old.binding_id
    _challenge(repo, session, "alice", "new-token")
    assert _consume(repo, session, "new-token", "UID_NEW") == "bound"
    current = repo.get_active(session, "alice", "wxpusher")
    assert current is not None
    assert current.binding_id == binding_id
    assert current.recipient_id == "UID_NEW"
    assert current.status == "active"
    assert current.revoked_at is None


def test_bind_verified_uses_same_idempotency_rebind_and_conflict_rules(repo, session) -> None:
    first = repo.bind_verified(
        session,
        user_id="alice",
        channel="telegram",
        recipient_id="CHAT_A",
        now=NOW,
    )
    again = repo.bind_verified(
        session,
        user_id="alice",
        channel="telegram",
        recipient_id="CHAT_A",
        now=NOW + timedelta(seconds=1),
    )
    rebound = repo.bind_verified(
        session,
        user_id="alice",
        channel="telegram",
        recipient_id="CHAT_NEW",
        now=NOW + timedelta(seconds=2),
    )
    assert again.binding_id == first.binding_id
    assert rebound.binding_id == first.binding_id
    assert rebound.recipient_id == "CHAT_NEW"
    with pytest.raises(ValueError) as exc_info:
        repo.bind_verified(
            session,
            user_id="bob",
            channel="telegram",
            recipient_id="CHAT_NEW",
            now=NOW,
        )
    assert exc_info.value.args == ("notification.binding_recipient_conflict",)
    assert exc_info.value.__cause__ is None


def test_offset_expiration_is_normalized_to_utc(repo, session) -> None:
    expires_at = datetime.fromisoformat("2026-09-17T16:00:00+08:00")
    _challenge(repo, session, "alice", "offset-token", expires_at=expires_at)

    assert (
        repo.consume_and_bind(
            session,
            token="offset-token",
            channel="wxpusher",
            recipient_id="UID_OFFSET",
            now=datetime.fromisoformat("2026-09-17T08:00:01+00:00"),
        )
        == "expired_or_used"
    )


def test_get_active_and_list_for_user_include_expected_channels(repo, session) -> None:
    repo.bind_verified(
        session,
        user_id="alice",
        channel="telegram",
        recipient_id="CHAT_A",
        now=NOW,
    )
    repo.bind_verified(
        session,
        user_id="alice",
        channel="wxpusher",
        recipient_id="UID_A",
        now=NOW,
    )
    assert repo.revoke(session, user_id="alice", channel="telegram", now=NOW)
    assert repo.get_active(session, "alice", "telegram") is None
    wx = repo.get_active(session, "alice", "wxpusher")
    assert wx is not None and wx.recipient_id == "UID_A"
    rows = repo.list_for_user(session, "alice")
    assert [(row.channel, row.status) for row in rows] == [
        ("telegram", "revoked"),
        ("wxpusher", "active"),
    ]


def test_revoke_invalidates_all_open_challenges_and_is_idempotent(repo, session) -> None:
    _bind(repo, session, "alice", "UID_A", "bind-token")
    pending = _challenge(repo, session, "alice", "pending-token")
    assert repo.revoke(session, user_id="alice", channel="wxpusher", now=NOW)
    assert pending.consumed_at is not None
    assert _consume(repo, session, "pending-token", "UID_A") == "expired_or_used"
    assert not repo.revoke(session, user_id="alice", channel="wxpusher", now=NOW)


def test_conditional_revoke_does_not_revoke_rebound_recipient(repo, session) -> None:
    _bind(repo, session, "alice", "UID_OLD", "old-token")
    _challenge(repo, session, "alice", "rebind-token")
    assert _consume(repo, session, "rebind-token", "UID_NEW") == "bound"
    assert not repo.revoke_if_current(session, "alice", "wxpusher", "UID_OLD", NOW)
    current = repo.get_active(session, "alice", "wxpusher")
    assert current is not None and current.recipient_id == "UID_NEW"
    assert repo.revoke_if_current(session, "alice", "wxpusher", "UID_NEW", NOW)
    assert repo.get_active(session, "alice", "wxpusher") is None


def test_set_provider_code_first_write_rolls_back_with_caller_transaction(repo, db) -> None:
    with db.Session() as seed:
        challenge = _challenge(repo, seed, "alice", "provider-rollback-token")
        seed.commit()
        challenge_id = challenge.challenge_id

    session = db.Session()
    try:
        assert repo.set_provider_code(session, challenge_id, "ROLLBACK_CODE")
        session.rollback()
    finally:
        session.close()
    with db.Session() as check:
        row = (
            check.query(orm.NotificationBindingChallengeModel)
            .filter_by(challenge_id=challenge_id)
            .one()
        )
        assert row.provider_code is None


def test_bind_verified_first_write_rolls_back_with_caller_transaction(repo, db) -> None:
    session = db.Session()
    try:
        repo.bind_verified(
            session,
            user_id="alice",
            channel="wxpusher",
            recipient_id="UID_FIRST_WRITE",
            now=NOW,
        )
        session.rollback()
    finally:
        session.close()
    with db.Session() as check:
        assert check.query(orm.UserNotificationBindingModel).count() == 0


def test_binding_and_challenge_are_rolled_back_with_caller_transaction(repo, db) -> None:
    session = db.Session()
    try:
        _challenge(repo, session, "alice", "rollback-token")
        assert _consume(repo, session, "rollback-token", "UID_ROLLBACK") == "bound"
        session.rollback()
    finally:
        session.close()
    with db.Session() as check:
        assert check.query(orm.NotificationBindingChallengeModel).count() == 0
        assert check.query(orm.UserNotificationBindingModel).count() == 0


def test_preference_create_and_update_honor_caller_transaction(db) -> None:
    from zhice_os_v2.infrastructure.repositories.notification_preference_repository import (
        NotificationPreferenceRepository,
    )

    repository = NotificationPreferenceRepository()
    session = db.Session()
    try:
        created = repository.create(
            "pref-rollback",
            "alice",
            channel="wxpusher",
            locale="zh",
            session=session,
        )
        assert created is not None
        session.rollback()
    finally:
        session.close()
    with db.Session() as check:
        assert check.query(orm.UserNotificationPreferenceModel).count() == 0
        check.add(
            orm.UserNotificationPreferenceModel(
                preference_id="pref-existing",
                user_id="alice",
                channel="wxpusher",
                market_reports_enabled={},
                system_alerts_enabled=True,
                quiet_hours=None,
                timezone="Asia/Shanghai",
                locale="zh",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        check.commit()
    session = db.Session()
    try:
        updated = repository.update(
            "alice",
            channel="wxpusher",
            fields={"locale": "en"},
            session=session,
        )
        assert updated is not None and updated.locale == "en"
        session.rollback()
    finally:
        session.close()
    with db.Session() as check:
        row = check.query(orm.UserNotificationPreferenceModel).one()
        assert row.locale == "zh"


def test_preference_create_and_update_keep_self_managed_compatibility(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zhice_os_v2.infrastructure.repositories.notification_preference_repository as pref_mod

    monkeypatch.setattr(pref_mod, "get_session", db.Session)
    repository = pref_mod.NotificationPreferenceRepository()
    created = repository.create("pref-committed", "alice", channel="wxpusher", locale="zh")
    updated = repository.update("alice", channel="wxpusher", fields={"locale": "en"})
    assert created is not None
    assert updated is not None and updated.locale == "en"
    with db.Session() as check:
        row = check.query(orm.UserNotificationPreferenceModel).one()
        assert row.locale == "en"


def test_domain_conflict_leaves_caller_transaction_usable(repo, session) -> None:
    _bind(repo, session, "alice", "UID_SHARED", "alice-token")
    _challenge(repo, session, "bob", "bob-token")
    with pytest.raises(ValueError, match="notification.binding_recipient_conflict"):
        _consume(repo, session, "bob-token", "UID_SHARED")
    repo.bind_verified(
        session,
        user_id="carol",
        channel="wxpusher",
        recipient_id="UID_C",
        now=NOW,
    )
    session.flush()
    assert session.is_active
    assert repo.get_active(session, "carol", "wxpusher") is not None
