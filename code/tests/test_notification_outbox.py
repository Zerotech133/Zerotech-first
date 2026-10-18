"""通知 outbox schema 测试（Phase C Task 2：四表 + chat_id 唯一约束，纯 additive）。

Schema 层验证：
- ORM metadata 含 challenge / preference / event / delivery 四表及关键列、
  unique(user_id,channel)、unique(event_id,user_id,channel)、复合索引
  (status,next_attempt_at)、status CHECK 枚举、v2_user.telegram_chat_id 唯一索引。
- create_all（内存 sqlite）后 event+delivery round-trip 读回一致（含五种状态枚举值）。
- 唯一约束防重复插入（IntegrityError）：delivery 三元组 / preference 二元组 /
  challenge token_hash / user telegram_chat_id。

Task 5 追加：Transactional outbox 服务（NotificationType 枚举 / 模式门 broadcast|
shadow|directed / 幂等 / 实例级与用户级偏好过滤 / shadow 路由日志不落敏感数据 /
调用方 session 同事务不自 commit / payload round-trip）。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.application.services.ownership_resolver import RuntimeOwner
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.base import Base

_NOW = datetime.now(UTC)


@pytest.fixture
def session():
    """隔离内存 sqlite（create_all 全 metadata，含新四表）。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _user(user_id: str, chat_id: str | None = None) -> orm.UserModel:
    return orm.UserModel(
        user_id=user_id,
        username=user_id,
        password_hash="x",
        role="TRADER",
        is_active=True,
        locale="zh",
        telegram_chat_id=chat_id,
        created_at=_NOW,
    )


def _add_event(session, event_id: str, user_id: str) -> orm.NotificationEventModel:
    event = orm.NotificationEventModel(
        event_id=event_id,
        user_id=user_id,
        event_type="fill",
        payload={"fill_id": "f1", "symbol": "AAPL"},
        occurred_at=_NOW,
        created_at=_NOW,
    )
    session.add(event)
    session.flush()
    return event


# ──────────────────────────────────────────────────────────────────────────────
# metadata：四表存在 + 关键列
# ──────────────────────────────────────────────────────────────────────────────
def test_metadata_contains_notification_tables() -> None:
    tables = Base.metadata.tables
    for name in (
        "v2_telegram_binding_challenge",
        "v2_user_notification_binding",
        "v2_notification_binding_challenge",
        "v2_user_notification_preference",
        "v2_notification_event",
        "v2_notification_delivery",
    ):
        assert name in tables, f"缺少表 {name}"

    challenge = tables["v2_telegram_binding_challenge"]
    assert set(challenge.columns.keys()) >= {
        "challenge_id", "user_id", "token_hash", "expires_at", "consumed_at", "created_at",
    }
    binding = tables["v2_user_notification_binding"]
    assert set(binding.columns.keys()) >= {
        "binding_id", "user_id", "channel", "recipient_id", "status",
        "bound_at", "revoked_at", "created_at", "updated_at",
    }
    generic_challenge = tables["v2_notification_binding_challenge"]
    assert set(generic_challenge.columns.keys()) >= {
        "challenge_id", "user_id", "channel", "token_hash", "provider_code",
        "expires_at", "consumed_at", "created_at",
    }
    preference = tables["v2_user_notification_preference"]
    assert set(preference.columns.keys()) >= {
        "preference_id", "user_id", "channel", "market_reports_enabled",
        "system_alerts_enabled", "signal_enabled", "fill_enabled",
        "morning_report_enabled", "evening_report_enabled", "quiet_hours",
        "timezone", "locale", "created_at", "updated_at",
    }
    event = tables["v2_notification_event"]
    assert set(event.columns.keys()) >= {
        "event_id", "user_strategy_id", "account_id", "user_id",
        "event_type", "payload", "occurred_at", "created_at",
    }
    delivery = tables["v2_notification_delivery"]
    assert set(delivery.columns.keys()) >= {
        "delivery_id", "event_id", "user_id", "channel", "status",
        "attempt_count", "next_attempt_at", "claimed_at", "last_error_code",
        "created_at", "sent_at",
    }


# ──────────────────────────────────────────────────────────────────────────────
# metadata：唯一约束 / 复合索引 / CHECK / FK 指向
# ──────────────────────────────────────────────────────────────────────────────
def test_metadata_delivery_constraints_and_indexes() -> None:
    delivery = Base.metadata.tables["v2_notification_delivery"]
    unique_sets = {
        tuple(col.name for col in uc.columns)
        for uc in delivery.constraints
        if isinstance(uc, UniqueConstraint)
    }
    # unique(event_id, user_id, channel)：outbox 幂等锚点
    assert ("event_id", "user_id", "channel") in unique_sets
    # CHECK 状态枚举（固定名 ck_v2_notification_delivery_status）
    assert any(
        isinstance(c, CheckConstraint) and c.name == "ck_v2_notification_delivery_status"
        for c in delivery.constraints
    )
    # worker claim 复合索引 (status, next_attempt_at)
    index_sets = {tuple(col.name for col in ix.columns) for ix in delivery.indexes}
    assert ("status", "next_attempt_at") in index_sets


def test_metadata_preference_and_user_constraints() -> None:
    preference = Base.metadata.tables["v2_user_notification_preference"]
    unique_sets = {
        tuple(col.name for col in uc.columns)
        for uc in preference.constraints
        if isinstance(uc, UniqueConstraint)
    }
    # unique(user_id, channel)：每用户每渠道一行
    assert ("user_id", "channel") in unique_sets
    for name in (
        "signal_enabled",
        "fill_enabled",
        "morning_report_enabled",
        "evening_report_enabled",
    ):
        column = preference.columns[name]
        assert column.nullable is False
        assert column.default is not None
        assert column.server_default is not None

    binding = Base.metadata.tables["v2_user_notification_binding"]
    binding_unique_sets = {
        tuple(col.name for col in uc.columns)
        for uc in binding.constraints
        if isinstance(uc, UniqueConstraint)
    }
    assert ("user_id", "channel") in binding_unique_sets
    assert ("channel", "recipient_id") in binding_unique_sets
    assert any(
        isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_v2_notification_binding_status"
        for constraint in binding.constraints
    )

    generic_challenge = Base.metadata.tables["v2_notification_binding_challenge"]
    challenge_unique_sets = {
        tuple(col.name for col in uc.columns)
        for uc in generic_challenge.constraints
        if isinstance(uc, UniqueConstraint)
    }
    assert ("channel", "provider_code") in challenge_unique_sets
    assert generic_challenge.columns["provider_code"].nullable is True

    # v2_user.telegram_chat_id 唯一索引（一个 chat 只能绑一个用户）
    user = Base.metadata.tables["v2_user"]
    chat_indexes = [
        ix for ix in user.indexes if [c.name for c in ix.columns] == ["telegram_chat_id"]
    ]
    assert chat_indexes, "缺少 v2_user.telegram_chat_id 唯一索引"
    assert all(ix.unique for ix in chat_indexes)


def test_metadata_fk_targets() -> None:
    event = Base.metadata.tables["v2_notification_event"]
    assert event.columns["user_strategy_id"].foreign_keys
    assert (
        list(event.columns["user_strategy_id"].foreign_keys)[0].target_fullname
        == "v2_user_strategy_instance.user_strategy_id"
    )
    assert event.columns["account_id"].foreign_keys
    assert (
        list(event.columns["account_id"].foreign_keys)[0].target_fullname
        == "v2_user_account.account_id"
    )
    delivery = Base.metadata.tables["v2_notification_delivery"]
    assert (
        list(delivery.columns["event_id"].foreign_keys)[0].target_fullname
        == "v2_notification_event.event_id"
    )
    challenge = Base.metadata.tables["v2_telegram_binding_challenge"]
    assert (
        list(challenge.columns["user_id"].foreign_keys)[0].target_fullname
        == "v2_user.user_id"
    )
    binding = Base.metadata.tables["v2_user_notification_binding"]
    assert (
        list(binding.columns["user_id"].foreign_keys)[0].target_fullname
        == "v2_user.user_id"
    )
    generic_challenge = Base.metadata.tables["v2_notification_binding_challenge"]
    assert (
        list(generic_challenge.columns["user_id"].foreign_keys)[0].target_fullname
        == "v2_user.user_id"
    )


# ──────────────────────────────────────────────────────────────────────────────
# round-trip（create_all 内存 sqlite）
# ──────────────────────────────────────────────────────────────────────────────
def test_event_delivery_round_trip(session) -> None:
    session.add(_user("u-notif-1"))
    session.flush()
    event = _add_event(session, "evt-1", "u-notif-1")
    delivery = orm.NotificationDeliveryModel(
        delivery_id="dlv-1",
        event_id="evt-1",
        user_id="u-notif-1",
        channel="telegram",
        status="SENDING",
        attempt_count=1,
        next_attempt_at=_NOW,
        claimed_at=_NOW,
        last_error_code="telegram.rate_limited",
        created_at=_NOW,
    )
    session.add(delivery)
    session.commit()

    row = session.get(orm.NotificationDeliveryModel, delivery.id)
    assert row is not None
    assert row.delivery_id == "dlv-1"
    assert row.event_id == "evt-1"
    assert row.user_id == "u-notif-1"
    assert row.channel == "telegram"
    assert row.status == "SENDING"
    assert row.attempt_count == 1
    assert row.claimed_at is not None
    assert row.last_error_code == "telegram.rate_limited"
    assert row.sent_at is None  # 未送达

    ev = session.get(orm.NotificationEventModel, event.id)
    assert ev is not None
    assert ev.payload == {"fill_id": "f1", "symbol": "AAPL"}
    assert ev.event_type == "fill"
    assert ev.user_strategy_id is None  # nullable 归属列默认空
    assert ev.account_id is None


@pytest.mark.parametrize("status", ["PENDING", "SENDING", "RETRY", "SENT", "FAILED"])
def test_delivery_status_enum_round_trip(session, status: str) -> None:
    """CHECK 枚举允许的全部五个状态值均可落库读回。"""
    session.add(_user("u-notif-enum"))
    session.flush()
    _add_event(session, f"evt-{status}", "u-notif-enum")
    session.add(
        orm.NotificationDeliveryModel(
            delivery_id=f"dlv-{status}",
            event_id=f"evt-{status}",
            user_id="u-notif-enum",
            channel="telegram",
            status=status,
            attempt_count=0,
            created_at=_NOW,
        )
    )
    session.commit()
    row = (
        session.query(orm.NotificationDeliveryModel)
        .filter_by(delivery_id=f"dlv-{status}")
        .one()
    )
    assert row.status == status


def test_preference_round_trip_with_defaults(session) -> None:
    session.add(_user("u-pref-1"))
    pref = orm.UserNotificationPreferenceModel(
        preference_id="pref-1",
        user_id="u-pref-1",
        created_at=_NOW,
        updated_at=_NOW,
    )
    session.add(pref)
    session.flush()
    # 服务器端默认值：channel=telegram / 报告关闭 / 系统告警开 / 上海时区 / 中文
    session.refresh(pref)
    assert pref.channel == "telegram"
    assert pref.market_reports_enabled == {}
    assert pref.system_alerts_enabled is True
    assert pref.timezone == "Asia/Shanghai"
    assert pref.locale == "zh"
    assert pref.quiet_hours is None


def test_challenge_round_trip(session) -> None:
    session.add(_user("u-chal-1"))
    session.add(
        orm.TelegramBindingChallengeModel(
            challenge_id="chal-1",
            user_id="u-chal-1",
            token_hash="a" * 64,
            expires_at=_NOW,
            created_at=_NOW,
        )
    )
    session.commit()
    row = (
        session.query(orm.TelegramBindingChallengeModel)
        .filter_by(challenge_id="chal-1")
        .one()
    )
    assert row.token_hash == "a" * 64
    assert row.consumed_at is None  # 未消费


# ──────────────────────────────────────────────────────────────────────────────
# 唯一约束防重复插入
# ──────────────────────────────────────────────────────────────────────────────
def test_delivery_duplicate_event_user_channel_rejected(session) -> None:
    session.add(_user("u-dup-1"))
    session.flush()
    _add_event(session, "evt-dup", "u-dup-1")
    session.add(
        orm.NotificationDeliveryModel(
            delivery_id="dlv-dup-a", event_id="evt-dup", user_id="u-dup-1",
            channel="telegram", status="PENDING", attempt_count=0, created_at=_NOW,
        )
    )
    session.commit()
    # 同 (event_id, user_id, channel) 二次插入 → IntegrityError（outbox 幂等锚点）
    session.add(
        orm.NotificationDeliveryModel(
            delivery_id="dlv-dup-b", event_id="evt-dup", user_id="u-dup-1",
            channel="telegram", status="PENDING", attempt_count=0, created_at=_NOW,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_delivery_rejects_unknown_status(session) -> None:
    """CHECK 约束拒绝枚举外的状态值。"""
    session.add(_user("u-bogus"))
    session.flush()
    _add_event(session, "evt-bogus", "u-bogus")
    session.add(
        orm.NotificationDeliveryModel(
            delivery_id="dlv-bogus", event_id="evt-bogus", user_id="u-bogus",
            channel="telegram", status="BOGUS", attempt_count=0, created_at=_NOW,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_preference_duplicate_user_channel_rejected(session) -> None:
    session.add(_user("u-pdup"))
    for pid in ("pref-dup-a", "pref-dup-b"):
        session.add(
            orm.UserNotificationPreferenceModel(
                preference_id=pid, user_id="u-pdup", created_at=_NOW, updated_at=_NOW,
            )
        )
        if pid == "pref-dup-a":
            session.commit()
    with pytest.raises(IntegrityError):
        session.commit()


def test_challenge_duplicate_token_hash_rejected(session) -> None:
    session.add(_user("u-tdup"))
    session.add(
        orm.TelegramBindingChallengeModel(
            challenge_id="chal-a", user_id="u-tdup", token_hash="same-hash",
            expires_at=_NOW, created_at=_NOW,
        )
    )
    session.commit()
    session.add(
        orm.TelegramBindingChallengeModel(
            challenge_id="chal-b", user_id="u-tdup", token_hash="same-hash",
            expires_at=_NOW, created_at=_NOW,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_user_duplicate_telegram_chat_id_rejected(session) -> None:
    """一个 Telegram chat 只能绑一个用户（绑定唯一性安全边界）。"""
    session.add(_user("u-chat-a", chat_id="1234567890"))
    session.commit()
    session.add(_user("u-chat-b", chat_id="1234567890"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_multiple_users_without_chat_id_allowed(session) -> None:
    """未绑定的用户（chat_id 为 NULL）不受唯一索引影响。"""
    session.add(_user("u-null-a"))
    session.add(_user("u-null-b"))
    session.commit()
    assert session.query(orm.UserModel).count() == 2


# ══════════════════════════════════════════════════════════════════════════════
# Phase C Task 5：Transactional outbox 服务
# （模式门 / 幂等 / 偏好过滤 / shadow 日志脱敏 / 调用方事务 / payload round-trip）
# ══════════════════════════════════════════════════════════════════════════════

_OUTBOX_LOGGER = "zhice_os_v2.application.services.notification_outbox_service"
_OCCURRED_AT = datetime.now(UTC)


@pytest.fixture
def outbox_env(monkeypatch: pytest.MonkeyPatch, users):
    """outbox 隔离环境：实例/账户/偏好/用户四仓储与 outbox 共用同一内存 sqlite。

    与 test_telegram_directed.py 的 preference_env 同范式（monkeypatch 各仓储模块
    get_session 覆盖 _guard_real_pg）；enqueue 显式传 session，不经 get_session。
    user_repository 也须 patch：Task 8 起 enqueue 前查 telegram 绑定（T7-M5），
    不 patch 时若 app 先被导入（user_repository 已绑定真 get_session）会直连真 PG
    造成依赖导入顺序的抖动；alice 落 user 行且带 chat_id（已绑定=可投递）。
    """
    import zhice_os_v2.infrastructure.repositories.notification_preference_repository as pref_repo_mod
    import zhice_os_v2.infrastructure.repositories.user_account_repository as acc_repo_mod
    import zhice_os_v2.infrastructure.repositories.user_repository as user_repo_mod
    import zhice_os_v2.infrastructure.repositories.user_strategy_repository as inst_repo_mod
    from zhice_os_v2.application.services.notification_outbox_service import (
        NotificationOutboxService,
    )
    from zhice_os_v2.application.services.notification_preference_service import (
        NotificationPreferenceService,
    )
    from zhice_os_v2.application.services.user_account_service import UserAccountService
    from zhice_os_v2.application.services.user_strategy_service import UserStrategyService
    from zhice_os_v2.infrastructure.db.base import Base as _Base
    from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    # sess_mod：Task 10 C-1 起路由期 binding 读走 _own_session（自开短命会话），
    # 须指向隔离 sqlite（同 wiring_env 五模块范式，防 _guard_real_pg 守卫拦截）。
    import zhice_os_v2.infrastructure.db.session as sess_mod

    monkeypatch.setattr(pref_repo_mod, "get_session", lambda: Session())
    monkeypatch.setattr(acc_repo_mod, "get_session", lambda: Session())
    monkeypatch.setattr(inst_repo_mod, "get_session", lambda: Session())
    monkeypatch.setattr(user_repo_mod, "get_session", lambda: Session())
    monkeypatch.setattr(sess_mod, "get_session", lambda: Session())

    s = Session()
    try:
        s.add(
            orm.UserModel(
                user_id=users.alice.user_id,
                username=users.alice.username,
                password_hash="x",
                role="TRADER",
                is_active=True,
                locale="zh",
                telegram_chat_id="10001",
                created_at=_NOW,
            )
        )
        s.flush()
        # Task 10 起 delivery 硬门 = v2_user_notification_binding（get_active），
        # 不再读 user.telegram_chat_id：种 active telegram 绑定行，alice 维持
        # 「已绑定 = 可投递」前提；wxpusher 不绑（本文件断言均为 telegram 单渠道）。
        s.add(
            orm.UserNotificationBindingModel(
                binding_id="nbind-outbox-env-telegram",
                user_id=users.alice.user_id,
                channel="telegram",
                recipient_id="10001",
                status="active",
                bound_at=_NOW,
                revoked_at=None,
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
        s.commit()
    finally:
        s.close()

    db = InMemoryDatabase()
    instance_service = UserStrategyService(db=db, account_service=UserAccountService())
    preference_service = NotificationPreferenceService()

    def make_outbox(mode: str | None = "directed") -> NotificationOutboxService:
        return NotificationOutboxService(mode=mode, instance_service=instance_service)

    return SimpleNamespace(
        Session=Session,
        db=db,
        users=users,
        instance_service=instance_service,
        preference_service=preference_service,
        make_outbox=make_outbox,
    )


def _public_version(db, name: str, market: str):
    from zhice_os_v2.application.services.strategy_catalog_service import (
        StrategyCatalogService,
    )

    catalog = StrategyCatalogService(db)
    strat = catalog.create_public(name, market=market)
    return next(
        v for v in db.list_strategy_versions() if v.strategy_id == strat.strategy_id
    )


def _enabled_instance(env, user, market: str = "US", *, name: str | None = None):
    """注册公共版本 → 建账户实例 → 启用（fill 等实例事件的可投递前提）。"""
    version = _public_version(env.db, name or f"pub-{market}", market)
    inst = env.instance_service.create(
        user, version.strategy_version_id, initial_capital=10_000
    )
    return env.instance_service.enable(user, inst.user_strategy_id)


def _owner_of(inst) -> RuntimeOwner:
    return RuntimeOwner(
        user_id=inst.user_id,
        user_strategy_id=inst.user_strategy_id,
        account_id=inst.account_id,
        strategy_version_id=inst.strategy_version_id,
    )


def _no_instance_owner(user_id: str) -> RuntimeOwner:
    """无实例事件（报告/平台级 runtime_error）的 owner：user_strategy_id 为 None。"""
    return RuntimeOwner(
        user_id=user_id,
        user_strategy_id=None,
        account_id=None,
        strategy_version_id=None,
    )


def _fill_kwargs(owner: RuntimeOwner, event_id: str) -> dict:
    return dict(
        event_id=event_id,
        owner=owner,
        event_type="fill",
        payload={"fill_id": f"f-{event_id}"},
        occurred_at=_OCCURRED_AT,
    )


# ── 枚举 ─────────────────────────────────────────────────────────────────────


def test_notification_type_enum_members() -> None:
    from zhice_os_v2.application.services.notification_outbox_service import (
        NotificationType,
    )

    expected = {
        "SIGNAL": "signal",
        "PLAN": "plan",
        "APPROVAL": "approval",
        "RISK": "risk",
        "ORDER": "order",
        "FILL": "fill",
        "STOP_LOSS": "stop_loss",
        "TAKE_PROFIT": "take_profit",
        "RUNTIME_ERROR": "runtime_error",
        "MARKET_REPORT": "market_report",
        "USER_REPORT": "user_report",
    }
    assert {m.name: m.value for m in NotificationType} == expected
    assert NotificationType.FILL == "fill"  # StrEnum 字符串互等（偏好键同源）
    assert NotificationType("take_profit") is NotificationType.TAKE_PROFIT


def test_enqueue_rejects_unknown_event_type(outbox_env) -> None:
    inst = _enabled_instance(outbox_env, outbox_env.users.alice)
    outbox = outbox_env.make_outbox("directed")
    s = outbox_env.Session()
    try:
        with pytest.raises(ValueError):
            outbox.enqueue(
                s,
                event_id="fill-bogus",
                owner=_owner_of(inst),
                event_type="definitely_not_event",
                payload={},
                occurred_at=_OCCURRED_AT,
            )
    finally:
        s.close()


# ── 计划红测试 1：幂等 ───────────────────────────────────────────────────────


def test_enqueue_is_idempotent(outbox_env) -> None:
    inst = _enabled_instance(outbox_env, outbox_env.users.alice)
    outbox = outbox_env.make_outbox("directed")
    s = outbox_env.Session()
    try:
        kw = _fill_kwargs(_owner_of(inst), "fill-1")
        outbox.enqueue(s, **kw)
        outbox.enqueue(s, **kw)  # 同事务内重放（调用方重试）
        assert outbox.delivery_count("fill-1", session=s) == 1
        assert outbox.event_exists("fill-1", session=s) is True
        s.commit()

        # 跨事务重放（另一会话再次 enqueue 同 event_id）：不新建 event/delivery
        s2 = outbox_env.Session()
        try:
            outbox.enqueue(s2, **kw)
            s2.commit()
            assert outbox.delivery_count("fill-1", session=s2) == 1
            assert (
                s2.query(orm.NotificationEventModel)
                .filter_by(event_id="fill-1")
                .count()
                == 1
            )
        finally:
            s2.close()
    finally:
        s.close()


def test_enqueue_does_not_commit_caller_transaction(
    outbox_env, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """outbox 不自 commit：行随调用方事务回滚一并消失（同生共死语义）。

    连接模型须与生产同构（每会话独立连接）：StaticPool 单连接下，enqueue 路由
    期开/关的只读会话（渠道偏好仓储 get / should_notify）会把调用方在途事务一并
    回滚（测试态伪影；生产 PG 独立连接无此问题），且被回滚后 SAVEPOINT 成为连接
    首语句、RELEASE SAVEPOINT 会直接提交（sqlite/pysqlite 边角，PG 无此行为）。
    故本用例换 file sqlite 独立连接，并按 _record_fill 真实形态：调用方事务先
    写一笔业务行再 enqueue。"""
    import zhice_os_v2.infrastructure.db.session as sess_mod
    import zhice_os_v2.infrastructure.repositories.notification_preference_repository as pref_repo_mod
    import zhice_os_v2.infrastructure.repositories.user_account_repository as acc_repo_mod
    import zhice_os_v2.infrastructure.repositories.user_repository as user_repo_mod
    import zhice_os_v2.infrastructure.repositories.user_strategy_repository as inst_repo_mod

    engine = create_engine(f"sqlite:///{tmp_path}/caller_tx.db")
    Base.metadata.create_all(engine)
    FileSession = sessionmaker(bind=engine, expire_on_commit=False)
    # sess_mod：C-1 起路由 binding 读自开会话（_guard_real_pg 会拦未 patch 的真库直连）
    for mod in (pref_repo_mod, acc_repo_mod, inst_repo_mod, user_repo_mod, sess_mod):
        monkeypatch.setattr(mod, "get_session", lambda: FileSession())

    s0 = FileSession()
    try:
        s0.add(
            orm.UserModel(
                user_id=outbox_env.users.alice.user_id,
                username=outbox_env.users.alice.username,
                password_hash="x",
                role="TRADER",
                is_active=True,
                locale="zh",
                telegram_chat_id="1001",
                created_at=_NOW,
            )
        )
        s0.commit()
    finally:
        s0.close()

    inst = _enabled_instance(outbox_env, outbox_env.users.alice)
    outbox = outbox_env.make_outbox("directed")
    s = FileSession()
    try:
        s.add(
            orm.PositionModel(
                position_id="pos-tx",
                strategy_version_id="v1-us-sv-tx",
                market="US",
                ts_code="AAPL",
                direction="LONG",
                open_qty=Decimal("10"),
                open_price=Decimal("100"),
                status="OPEN",
                opened_at=_OCCURRED_AT,
            )
        )
        s.flush()  # 业务写在途（未 commit）——enqueue 前的真实调用方形态
        outbox.enqueue(s, **_fill_kwargs(_owner_of(inst), "fill-tx"))
        s.rollback()
        assert outbox.event_exists("fill-tx", session=s) is False
        assert outbox.delivery_count("fill-tx", session=s) == 0
        assert s.query(orm.PositionModel).filter_by(position_id="pos-tx").count() == 0
    finally:
        s.close()
        engine.dispose()


# ── 计划红测试 2：偏好关 → 有 event 无 delivery ──────────────────────────────


def test_disabled_preference_creates_event_without_delivery(outbox_env) -> None:
    inst = _enabled_instance(outbox_env, outbox_env.users.alice)
    outbox_env.instance_service.update_notifications(
        outbox_env.users.alice, inst.user_strategy_id, {"fill": False}
    )
    outbox = outbox_env.make_outbox("directed")
    s = outbox_env.Session()
    try:
        outbox.enqueue(s, **_fill_kwargs(_owner_of(inst), "fill-off"))
        s.commit()
        assert outbox.event_exists("fill-off", session=s) is True
        assert outbox.delivery_count("fill-off", session=s) == 0
    finally:
        s.close()


def test_instance_event_without_instance_fails_closed(outbox_env) -> None:
    """实例级事件但 owner 无实例（legacy/断链）→ fail-closed：有 event 无 delivery。"""
    outbox = outbox_env.make_outbox("directed")
    s = outbox_env.Session()
    try:
        outbox.enqueue(
            s,
            event_id="fill-noinst",
            owner=_no_instance_owner(outbox_env.users.alice.user_id),
            event_type="fill",
            payload={"fill_id": "f-noinst"},
            occurred_at=_OCCURRED_AT,
        )
        s.commit()
        assert outbox.event_exists("fill-noinst", session=s) is True
        assert outbox.delivery_count("fill-noinst", session=s) == 0
    finally:
        s.close()


# ── 模式门 ───────────────────────────────────────────────────────────────────


def test_broadcast_mode_is_noop(outbox_env) -> None:
    """broadcast（默认现状广播由调用方直接 broadcast_telegram）：outbox 什么都不写。"""
    inst = _enabled_instance(outbox_env, outbox_env.users.alice)
    outbox = outbox_env.make_outbox("broadcast")
    s = outbox_env.Session()
    try:
        outbox.enqueue(s, **_fill_kwargs(_owner_of(inst), "fill-bc"))
        s.commit()
        assert outbox.event_exists("fill-bc", session=s) is False
        assert outbox.delivery_count("fill-bc", session=s) == 0
    finally:
        s.close()


def test_shadow_mode_writes_event_without_delivery_and_logs(
    outbox_env, caplog: pytest.LogCaptureFixture
) -> None:
    """shadow：event 有 / delivery 无；路由日志只落五字段（偏好关 → eligible=False）。"""
    inst = _enabled_instance(outbox_env, outbox_env.users.alice)
    outbox_env.instance_service.update_notifications(
        outbox_env.users.alice, inst.user_strategy_id, {"fill": False}
    )
    outbox = outbox_env.make_outbox("shadow")
    s = outbox_env.Session()
    try:
        with caplog.at_level(logging.INFO, logger=_OUTBOX_LOGGER):
            outbox.enqueue(
                s,
                event_id="fill-sh",
                owner=_owner_of(inst),
                event_type="fill",
                payload={"fill_id": "SECRET-FILL-MARKER"},
                occurred_at=_OCCURRED_AT,
            )
            s.commit()
        assert outbox.event_exists("fill-sh", session=s) is True
        assert outbox.delivery_count("fill-sh", session=s) == 0
    finally:
        s.close()

    shadow_records = [
        r
        for r in caplog.records
        if "notification_shadow" in r.message and "channel=telegram" in r.message
    ]
    # Task 10 起逐渠道各一条路由日志（telegram 实例偏好判定 + wxpusher no_binding）；
    # 语义不变：偏好关 → telegram 渠道 eligible=False。
    assert len(shadow_records) == 1
    msg = shadow_records[0].getMessage()
    assert "event_id=fill-sh" in msg
    assert f"user_id={outbox_env.users.alice.user_id}" in msg
    assert "event_type=fill" in msg
    assert "eligible=False" in msg
    assert "reason=instance_preference_disabled" in msg
    # 安全铁律：日志绝不落 chat_id / payload 正文
    assert "chat_id" not in msg
    assert "SECRET-FILL-MARKER" not in msg


def test_shadow_log_eligible_true_when_preference_on(
    outbox_env, caplog: pytest.LogCaptureFixture
) -> None:
    inst = _enabled_instance(outbox_env, outbox_env.users.alice)
    outbox = outbox_env.make_outbox("shadow")
    s = outbox_env.Session()
    try:
        with caplog.at_level(logging.INFO, logger=_OUTBOX_LOGGER):
            outbox.enqueue(
                s,
                event_id="fill-sh2",
                owner=_owner_of(inst),
                event_type="fill",
                payload={},
                occurred_at=_OCCURRED_AT,
            )
            s.commit()
        assert outbox.delivery_count("fill-sh2", session=s) == 0  # shadow 永不投递
    finally:
        s.close()
    msg = [
        r
        for r in caplog.records
        if "notification_shadow" in r.message and "channel=telegram" in r.message
    ][0].getMessage()
    assert "eligible=True" in msg
    assert "reason=eligible" in msg
    assert "chat_id" not in msg


def test_directed_mode_creates_pending_delivery(outbox_env) -> None:
    inst = _enabled_instance(outbox_env, outbox_env.users.alice)
    outbox = outbox_env.make_outbox("directed")
    s = outbox_env.Session()
    try:
        outbox.enqueue(s, **_fill_kwargs(_owner_of(inst), "fill-d1"))
        s.commit()
        deliveries = outbox.deliveries_for("fill-d1", session=s)
        assert len(deliveries) == 1
        d = deliveries[0]
        assert d.user_id == outbox_env.users.alice.user_id
        assert d.channel == "telegram"
        assert d.status == "PENDING"
        assert d.attempt_count == 0
        assert d.last_error_code is None
        assert d.delivery_id  # worker 认领锚点
    finally:
        s.close()


def test_mode_read_from_settings_when_not_overridden(
    outbox_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mode 缺省每调用现读 settings（不缓存）：patch settings 即生效。"""
    import zhice_os_v2.config.settings as settings_mod

    monkeypatch.setattr(
        settings_mod,
        "get_settings",
        lambda: SimpleNamespace(telegram_notification_mode="shadow"),
    )
    inst = _enabled_instance(outbox_env, outbox_env.users.alice)
    outbox = outbox_env.make_outbox(None)
    s = outbox_env.Session()
    try:
        outbox.enqueue(s, **_fill_kwargs(_owner_of(inst), "fill-cfg"))
        s.commit()
        assert outbox.event_exists("fill-cfg", session=s) is True
        assert outbox.delivery_count("fill-cfg", session=s) == 0  # shadow 语义
    finally:
        s.close()


# ── 用户级偏好路由（无实例事件）──────────────────────────────────────────────


def test_market_report_routes_on_user_market_preference(outbox_env) -> None:
    """MARKET_REPORT：按 payload["market"] 的用户市场偏好 opt-in，fail-closed。"""
    owner = _no_instance_owner(outbox_env.users.alice.user_id)
    outbox = outbox_env.make_outbox("directed")
    s = outbox_env.Session()
    try:
        def _enqueue(event_id: str, payload: dict) -> None:
            outbox.enqueue(
                s,
                event_id=event_id,
                owner=owner,
                event_type="market_report",
                payload=payload,
                occurred_at=_OCCURRED_AT,
            )

        # 缺省偏好 {} = 全市场关（opt-in）
        _enqueue("mr-1", {"market": "US"})
        assert outbox.delivery_count("mr-1", session=s) == 0
        assert outbox.event_exists("mr-1", session=s) is True

        # 开 US → 投递
        outbox_env.preference_service.set_market_report(
            outbox_env.users.alice, "US", True
        )
        _enqueue("mr-2", {"market": "US"})
        assert outbox.delivery_count("mr-2", session=s) == 1

        # 市场隔离：开 US ≠ 开 HK
        _enqueue("mr-3", {"market": "HK"})
        assert outbox.delivery_count("mr-3", session=s) == 0

        # payload 缺 market 键 → fail-closed
        _enqueue("mr-4", {})
        assert outbox.delivery_count("mr-4", session=s) == 0
        s.commit()
    finally:
        s.close()


def test_user_report_requires_any_market_opt_in(outbox_env) -> None:
    """USER_REPORT（用户维度聚合报告）：任一市场报告开启即订阅报告族；缺省全关。"""
    owner = _no_instance_owner(outbox_env.users.alice.user_id)
    outbox = outbox_env.make_outbox("directed")
    s = outbox_env.Session()
    try:
        outbox.enqueue(
            s,
            event_id="ur-1",
            owner=owner,
            event_type="user_report",
            payload={},
            occurred_at=_OCCURRED_AT,
        )
        assert outbox.delivery_count("ur-1", session=s) == 0

        outbox_env.preference_service.set_market_report(
            outbox_env.users.alice, "CRYPTO", True
        )
        outbox.enqueue(
            s,
            event_id="ur-2",
            owner=owner,
            event_type="user_report",
            payload={},
            occurred_at=_OCCURRED_AT,
        )
        s.commit()
        assert outbox.delivery_count("ur-2", session=s) == 1
    finally:
        s.close()


def test_runtime_error_without_instance_uses_system_alerts(outbox_env) -> None:
    """平台级 runtime_error（无实例）：按用户 system_alerts_enabled（缺省开）。"""
    from zhice_os_v2.application.services.notification_preference_service import (
        NotificationPreferenceBody,
    )

    owner = _no_instance_owner(outbox_env.users.alice.user_id)
    outbox = outbox_env.make_outbox("directed")
    s = outbox_env.Session()
    try:
        outbox.enqueue(
            s,
            event_id="re-1",
            owner=owner,
            event_type="runtime_error",
            payload={"source": "scheduler"},
            occurred_at=_OCCURRED_AT,
        )
        assert outbox.delivery_count("re-1", session=s) == 1  # 缺省 system_alerts=True

        outbox_env.preference_service.update(
            outbox_env.users.alice.user_id,
            NotificationPreferenceBody(system_alerts_enabled=False),
        )
        outbox.enqueue(
            s,
            event_id="re-2",
            owner=owner,
            event_type="runtime_error",
            payload={"source": "scheduler"},
            occurred_at=_OCCURRED_AT,
        )
        s.commit()
        assert outbox.event_exists("re-2", session=s) is True
        assert outbox.delivery_count("re-2", session=s) == 0
    finally:
        s.close()


# ── payload round-trip / session=None 退化 / 便捷读 ──────────────────────────


def test_payload_round_trip(outbox_env) -> None:
    inst = _enabled_instance(outbox_env, outbox_env.users.alice)
    payload = {
        "fill_id": "f-rt",
        "symbol": "AAPL",
        "price": 123.45,
        "qty": 10,
        "side": "BUY",
        "nested": {"tags": ["us", "breakout"]},
    }
    outbox = outbox_env.make_outbox("directed")
    s = outbox_env.Session()
    try:
        outbox.enqueue(
            s,
            event_id="fill-rt",
            owner=_owner_of(inst),
            event_type="fill",
            payload=payload,
            occurred_at=_OCCURRED_AT,
        )
        s.commit()
        ev = s.query(orm.NotificationEventModel).filter_by(event_id="fill-rt").one()
        assert ev.payload == payload
        assert ev.event_type == "fill"
        assert ev.user_id == outbox_env.users.alice.user_id
        assert ev.user_strategy_id == inst.user_strategy_id
        assert ev.occurred_at is not None
    finally:
        s.close()


def test_enqueue_without_session_commits_own_transaction(
    outbox_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """session=None 退化：自开 session 并 commit（非事务调用方/运维脚本用）。"""
    import zhice_os_v2.infrastructure.db.session as sess_mod

    monkeypatch.setattr(sess_mod, "get_session", lambda: outbox_env.Session())
    inst = _enabled_instance(outbox_env, outbox_env.users.alice)
    outbox = outbox_env.make_outbox("directed")
    outbox.enqueue(None, **_fill_kwargs(_owner_of(inst), "fill-auto"))

    s2 = outbox_env.Session()
    try:
        assert outbox.event_exists("fill-auto", session=s2) is True
        assert outbox.delivery_count("fill-auto", session=s2) == 1
    finally:
        s2.close()
