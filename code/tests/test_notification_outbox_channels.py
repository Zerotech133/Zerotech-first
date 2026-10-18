"""通知 outbox 多渠道路由与逐渠道 SAVEPOINT（多渠道改造 Task 10）。

覆盖（契约最小测试集 + 补充）：
- 路由结构：wxpusher-only 用户只产出一条 wxpusher delivery；双绑定用户两渠道
  各自独立 delivery（幂等锚点 unique(event_id,user_id,channel) 逐渠道成立）；
- 渠道级开关（Task 5 偏好对齐）：wxpusher signal_enabled=False → 只投 telegram；
  双渠道全关 → 零 delivery、event 恒落库（shadow 对照/审计要完整事件流）；
- 实例级 ∩ 渠道级：实例 fill 偏好关 → 双渠道都无（两者交集语义）；
- 报告类：市场 opt-in 关 → 无 delivery；morning_report_enabled=False 且
  payload.report_kind="morning" → 无该渠道 delivery（evening 照投）；
- system_alerts 既有语义保留：读 telegram 行的全局开关（域对象注释明确该标志
  非渠道级），关 → 双渠道都无；
- fail-closed：binding 查询异常 / preference 查询异常 → 零 delivery、event 仍写、
  不向上抛污染业务事务；
- 逐渠道 SAVEPOINT：单渠道 delivery INSERT 语句级 DB 错 → 只丢该渠道 delivery，
  另一渠道 delivery、event、业务 fill 行全部保留；失败日志只含
  event_id/channel/error_class 白名单，绝不含 payload / recipient。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.application.services.ownership_resolver import RuntimeOwner
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.base import Base

_NOW = datetime.now(UTC)
_OCCURRED_AT = datetime.now(UTC)
_OUTBOX_LOGGER = "zhice_os_v2.application.services.notification_outbox_service"


# ══════════════════════════════════════════════════════════════════════════════
# 隔离环境：偏好/账户/实例/用户仓储 + outbox 共用同一内存 sqlite（同 outbox_env
# 范式），外加渠道绑定 seeding 与带注入槽的 outbox 工厂。
# ══════════════════════════════════════════════════════════════════════════════
@pytest.fixture
def channels_env(monkeypatch: pytest.MonkeyPatch, users):
    import zhice_os_v2.infrastructure.db.session as sess_mod
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
    from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    for mod in (pref_repo_mod, acc_repo_mod, inst_repo_mod, user_repo_mod, sess_mod):
        monkeypatch.setattr(mod, "get_session", lambda: Session())

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
                created_at=_NOW,
            )
        )
        s.commit()
    finally:
        s.close()

    def bind(user_id: str, channel: str, recipient_id: str) -> None:
        """种一条 active 渠道绑定（路由的硬门数据源 = v2_user_notification_binding）。"""
        s = Session()
        try:
            s.add(
                orm.UserNotificationBindingModel(
                    binding_id=f"nbind-{channel}-{recipient_id}",
                    user_id=user_id,
                    channel=channel,
                    recipient_id=recipient_id,
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

    def make_outbox(
        mode: str | None = "directed",
        *,
        binding_repository=None,
        preference_repository=None,
    ) -> NotificationOutboxService:
        return NotificationOutboxService(
            mode=mode,
            instance_service=instance_service,
            preference_repository=preference_repository,
            binding_repository=binding_repository,
        )

    return SimpleNamespace(
        Session=Session,
        db=db,
        users=users,
        bind=bind,
        instance_service=instance_service,
        preference_service=NotificationPreferenceService(),
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
    """公共版本 → 账户实例 → 启用（fill 等实例事件的可投递前提）。"""
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
    """无实例事件（订阅 signal / 报告 / 平台级 runtime_error）的 owner。"""
    return RuntimeOwner(
        user_id=user_id,
        user_strategy_id=None,
        account_id=None,
        strategy_version_id=None,
    )


def _channels(outbox, event_id: str, *, session) -> list[str]:
    return [d.channel for d in outbox.deliveries_for(event_id, session=session)]


def _signal_kwargs(owner: RuntimeOwner, event_id: str) -> dict:
    return dict(
        event_id=event_id,
        owner=owner,
        event_type="signal",
        payload={"signal": "entry", "ts_code": "AAPL", "market": "US"},
        occurred_at=_OCCURRED_AT,
    )


def _fill_kwargs(owner: RuntimeOwner, event_id: str) -> dict:
    return dict(
        event_id=event_id,
        owner=owner,
        event_type="fill",
        payload={"fill_id": f"f-{event_id}"},
        occurred_at=_OCCURRED_AT,
    )


def _set_channel_switch(env, user_id: str, channel: str, **switches: bool) -> None:
    from zhice_os_v2.application.services.notification_preference_service import (
        NotificationPreferenceBody,
    )

    env.preference_service.update(
        user_id, NotificationPreferenceBody(**switches), channel=channel
    )


# ══════════════════════════════════════════════════════════════════════════════
# 契约最小测试集
# ══════════════════════════════════════════════════════════════════════════════
def test_wxpusher_only_user_gets_one_wxpusher_delivery(channels_env) -> None:
    """只绑 wxpusher（signal 开）→ 订阅 signal 只产出一条 wxpusher delivery。"""
    env = channels_env
    env.bind(env.users.alice.user_id, "wxpusher", "UID_WX")
    _set_channel_switch(env, env.users.alice.user_id, "wxpusher", signal_enabled=True)

    outbox = env.make_outbox("directed")
    s = env.Session()
    try:
        outbox.enqueue(
            s, **_signal_kwargs(_no_instance_owner(env.users.alice.user_id), "sig-wx1")
        )
        s.commit()
        assert _channels(outbox, "sig-wx1", session=s) == ["wxpusher"]
        assert outbox.event_exists("sig-wx1", session=s) is True
    finally:
        s.close()


def test_user_with_both_bindings_gets_two_independent_deliveries(channels_env) -> None:
    """双绑定 → enqueue_fill 产出 telegram+wxpusher 两条独立 PENDING delivery。"""
    env = channels_env
    env.bind(env.users.alice.user_id, "telegram", "10001")
    env.bind(env.users.alice.user_id, "wxpusher", "UID_WX")
    inst = _enabled_instance(env, env.users.alice)

    outbox = env.make_outbox("directed")
    s = env.Session()
    try:
        outbox.enqueue(s, **_fill_kwargs(_owner_of(inst), "fill-both"))
        s.commit()
        deliveries = outbox.deliveries_for("fill-both", session=s)
        assert {d.channel for d in deliveries} == {"telegram", "wxpusher"}
        assert {d.user_id for d in deliveries} == {env.users.alice.user_id}
        assert all(d.status == "PENDING" for d in deliveries)
    finally:
        s.close()


def test_one_channel_insert_failure_preserves_other_delivery_and_business_row(
    channels_env,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """单渠道 delivery INSERT 语句级 DB 错 → 只丢该渠道；另一渠道 delivery、event、
    业务 fill 行全部保留（逐渠道 SAVEPOINT）；失败日志白名单无 payload/recipient。

    连接模型与生产同构（file sqlite 每会话独立连接）：路由期读会话不会回滚调用方
    在途事务（StaticPool 单连接的测试态伪影，见 test_notification_wiring 同款说明）。
    """
    env = channels_env

    import sqlalchemy as sa

    import zhice_os_v2.application.services.notification_outbox_service as outbox_mod
    import zhice_os_v2.infrastructure.db.session as sess_mod
    import zhice_os_v2.infrastructure.repositories.notification_preference_repository as pref_repo_mod
    import zhice_os_v2.infrastructure.repositories.user_account_repository as acc_repo_mod
    import zhice_os_v2.infrastructure.repositories.user_repository as user_repo_mod
    import zhice_os_v2.infrastructure.repositories.user_strategy_repository as inst_repo_mod

    engine = create_engine(f"sqlite:///{tmp_path}/channel_savepoint.db")
    Base.metadata.create_all(engine)
    FileSession = sessionmaker(bind=engine, expire_on_commit=False)
    for mod in (pref_repo_mod, acc_repo_mod, inst_repo_mod, user_repo_mod, sess_mod):
        monkeypatch.setattr(mod, "get_session", lambda: FileSession())

    alice = env.users.alice
    s0 = FileSession()
    try:
        s0.add(
            orm.UserModel(
                user_id=alice.user_id,
                username=alice.username,
                password_hash="x",
                role="TRADER",
                is_active=True,
                locale="zh",
                created_at=_NOW,
            )
        )
        s0.flush()
        for channel, recipient in (("telegram", "10001"), ("wxpusher", "UID_WX")):
            s0.add(
                orm.UserNotificationBindingModel(
                    binding_id=f"nbind-sp-{channel}",
                    user_id=alice.user_id,
                    channel=channel,
                    recipient_id=recipient,
                    status="active",
                    bound_at=_NOW,
                    revoked_at=None,
                    created_at=_NOW,
                    updated_at=_NOW,
                )
            )
        s0.commit()
    finally:
        s0.close()

    inst = _enabled_instance(env, alice)
    outbox = env.make_outbox("directed")

    # wxpusher 的 delivery INSERT 注入真实语句级 DB 错（sqlite/PG 都炸且留在事务内）；
    # 纯 Python raise 毒不到事务，测不出逐渠道 SAVEPOINT。
    _original_insert = outbox_mod.NotificationOutboxService._insert_on_conflict_do_nothing

    def _failing_wxpusher_insert(session, model, values, *, index_elements):
        if (
            model is orm.NotificationDeliveryModel
            and values.get("channel") == "wxpusher"
        ):
            session.execute(sa.text("INSERT INTO v2_no_such_table_for_test (id) VALUES (1)"))
            raise AssertionError("unreachable: 上一行必然抛 DB 语句错")
        return _original_insert(session, model, values, index_elements=index_elements)

    monkeypatch.setattr(
        outbox_mod.NotificationOutboxService,
        "_insert_on_conflict_do_nothing",
        staticmethod(_failing_wxpusher_insert),
    )

    fill_id = "fill-sp"
    s = FileSession()
    try:
        s.add(
            orm.FillRecordModel(
                id=1,
                fill_id=fill_id,
                execution_id="exec-sp",
                market="US",
                ts_code="AAPL",
                broker_trade_id="SIM-sp",
                fill_price=Decimal("101"),
                fill_qty=Decimal("10"),
                fill_amount=Decimal("1010"),
                fill_time=_NOW,
                realized_pnl=Decimal("10"),
                sell_reason="离场",
                created_at=_NOW,
            )
        )
        s.flush()  # 业务写在途（未 commit）——enqueue 前的真实调用方形态
        with caplog.at_level(logging.WARNING, logger=_OUTBOX_LOGGER):
            outbox.enqueue(
                s,
                event_id=fill_id,
                owner=_owner_of(inst),
                event_type="fill",
                payload={"fill_id": "SECRET-FILL-MARKER"},
                occurred_at=_OCCURRED_AT,
            )
        s.commit()  # 单渠道 delivery 失败绝不毒化外层成交事务
    finally:
        s.close()

    check = FileSession()
    try:
        assert (
            check.query(orm.FillRecordModel).filter_by(fill_id=fill_id).one_or_none()
            is not None
        )
        assert check.query(orm.NotificationEventModel).filter_by(event_id=fill_id).count() == 1
        deliveries = (
            check.query(orm.NotificationDeliveryModel).filter_by(event_id=fill_id).all()
        )
        assert [d.channel for d in deliveries] == ["telegram"]  # 只丢 wxpusher
    finally:
        check.close()

    failures = [
        r for r in caplog.records if "notification_delivery_enqueue_failed" in r.message
    ]
    assert len(failures) == 1
    msg = failures[0].getMessage()
    assert f"event_id={fill_id}" in msg
    assert "channel=wxpusher" in msg
    assert "error_class=" in msg
    # 安全铁律：失败日志绝不落 payload 正文 / recipient
    assert "SECRET-FILL-MARKER" not in msg
    assert "UID_WX" not in msg


def test_binding_query_failure_is_fail_closed(channels_env) -> None:
    """binding 查询异常 → 双渠道都不建 delivery（fail-closed）、event 仍写、不上抛。"""
    from zhice_os_v2.infrastructure.repositories.notification_binding_repository import (
        NotificationBindingRepository,
    )

    class _BrokenBindingRepository(NotificationBindingRepository):
        def get_active(self, session, user_id, channel):  # type: ignore[no-untyped-def]
            raise RuntimeError("binding store down")

    env = channels_env
    outbox = env.make_outbox("directed", binding_repository=_BrokenBindingRepository())
    s = env.Session()
    try:
        outbox.enqueue(
            s, **_signal_kwargs(_no_instance_owner(env.users.alice.user_id), "sig-bqf")
        )
        s.commit()
        assert _channels(outbox, "sig-bqf", session=s) == []
        assert outbox.event_exists("sig-bqf", session=s) is True
    finally:
        s.close()


def test_binding_statement_error_uses_own_session_and_never_poisons_caller(
    channels_env,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """C-1 复核修复：路由期 binding 读必须走自开短命会话。

    真实语句级 DB 错（SELECT 不存在的表——纯 Python raise 毒不到事务，测不出
    本缺陷）只毒化该自开会话（用后即弃）；caller 事务零污染：enqueue 不上抛、
    成交行照常落库、零 delivery、event 恒写。核心红点：注入仓储收到的会话
    **绝不是 caller session**（修复前 binding 读在 caller session 上执行，PG
    语句级错会把调用方事务置 aborted → SAVEPOINT 建立失败 → rollback 降级为
    整事务回滚 → 调用方 commit "成功"但成交行静默丢失）。

    连接模型与生产同构（file sqlite 每会话独立连接）。
    """
    env = channels_env

    import sqlalchemy as sa

    import zhice_os_v2.infrastructure.db.session as sess_mod
    import zhice_os_v2.infrastructure.repositories.notification_preference_repository as pref_repo_mod
    import zhice_os_v2.infrastructure.repositories.user_account_repository as acc_repo_mod
    import zhice_os_v2.infrastructure.repositories.user_repository as user_repo_mod
    import zhice_os_v2.infrastructure.repositories.user_strategy_repository as inst_repo_mod
    from zhice_os_v2.infrastructure.repositories.notification_binding_repository import (
        NotificationBindingRepository,
    )

    engine = create_engine(f"sqlite:///{tmp_path}/binding_poison.db")
    Base.metadata.create_all(engine)
    FileSession = sessionmaker(bind=engine, expire_on_commit=False)
    for mod in (pref_repo_mod, acc_repo_mod, inst_repo_mod, user_repo_mod, sess_mod):
        monkeypatch.setattr(mod, "get_session", lambda: FileSession())

    alice = env.users.alice
    s0 = FileSession()
    try:
        s0.add(
            orm.UserModel(
                user_id=alice.user_id,
                username=alice.username,
                password_hash="x",
                role="TRADER",
                is_active=True,
                locale="zh",
                created_at=_NOW,
            )
        )
        s0.commit()
    finally:
        s0.close()

    inst = _enabled_instance(env, alice)

    sessions_seen: list[object] = []

    class _StatementErrorBindingRepository(NotificationBindingRepository):
        def get_active(self, session, user_id, channel):  # type: ignore[no-untyped-def]
            sessions_seen.append(session)
            session.execute(sa.text("SELECT 1 FROM v2_no_such_table_for_test"))
            raise AssertionError("unreachable: 上一行必然抛 DB 语句错")

    outbox = env.make_outbox(
        "directed", binding_repository=_StatementErrorBindingRepository()
    )

    fill_id = "fill-c1"
    s = FileSession()
    try:
        s.add(
            orm.FillRecordModel(
                id=1,
                fill_id=fill_id,
                execution_id="exec-c1",
                market="US",
                ts_code="AAPL",
                broker_trade_id="SIM-c1",
                fill_price=Decimal("101"),
                fill_qty=Decimal("10"),
                fill_amount=Decimal("1010"),
                fill_time=_NOW,
                realized_pnl=Decimal("10"),
                sell_reason="离场",
                created_at=_NOW,
            )
        )
        s.flush()  # 业务写在途（未 commit）——enqueue 前的真实调用方形态
        outbox.enqueue(  # 不上抛：语句级错被吸收在自开会话上（fail-closed）
            s,
            event_id=fill_id,
            owner=_owner_of(inst),
            event_type="fill",
            payload={"fill_id": "f-c1"},
            occurred_at=_OCCURRED_AT,
        )
        s.commit()
    finally:
        s.close()

    check = FileSession()
    try:
        assert (
            check.query(orm.FillRecordModel).filter_by(fill_id=fill_id).one_or_none()
            is not None
        ), "binding 语句级错后成交必须照常落库"
        assert check.query(orm.NotificationDeliveryModel).count() == 0
        assert check.query(orm.NotificationEventModel).filter_by(event_id=fill_id).count() == 1
    finally:
        check.close()

    # C-1 核心断言：binding 读绝不使用 caller session（语句级错只落在可丢弃的
    # 自开短命会话上，绝不波及调用方事务）。
    assert sessions_seen, "注入的 binding 仓储未被调用"
    assert all(
        seen is not s for seen in sessions_seen
    ), "binding 读使用了 caller session——语句级错会毒化调用方事务（C-1）"


# ══════════════════════════════════════════════════════════════════════════════
# 渠道级开关 / 实例级∩渠道级 / 报告类 / system_alerts / no_binding
# ══════════════════════════════════════════════════════════════════════════════
def test_channel_switch_off_excludes_only_that_channel(channels_env) -> None:
    """wxpusher signal_enabled=False → 只投 telegram（渠道级开关逐渠道独立）。"""
    env = channels_env
    env.bind(env.users.alice.user_id, "telegram", "10001")
    env.bind(env.users.alice.user_id, "wxpusher", "UID_WX")
    _set_channel_switch(env, env.users.alice.user_id, "wxpusher", signal_enabled=False)

    outbox = env.make_outbox("directed")
    s = env.Session()
    try:
        outbox.enqueue(
            s, **_signal_kwargs(_no_instance_owner(env.users.alice.user_id), "sig-1off")
        )
        s.commit()
        assert _channels(outbox, "sig-1off", session=s) == ["telegram"]
    finally:
        s.close()


def test_all_channels_switched_off_writes_event_without_delivery(channels_env) -> None:
    """双渠道 signal 全关 → 零 delivery；event 恒落库（既有语义）。"""
    env = channels_env
    env.bind(env.users.alice.user_id, "telegram", "10001")
    env.bind(env.users.alice.user_id, "wxpusher", "UID_WX")
    _set_channel_switch(env, env.users.alice.user_id, "telegram", signal_enabled=False)
    _set_channel_switch(env, env.users.alice.user_id, "wxpusher", signal_enabled=False)

    outbox = env.make_outbox("directed")
    s = env.Session()
    try:
        outbox.enqueue(
            s, **_signal_kwargs(_no_instance_owner(env.users.alice.user_id), "sig-alloff")
        )
        s.commit()
        assert _channels(outbox, "sig-alloff", session=s) == []
        assert outbox.event_exists("sig-alloff", session=s) is True
    finally:
        s.close()


def test_instance_preference_off_blocks_both_channels(channels_env) -> None:
    """实例级 fill 偏好关 → 双渠道都无（实例级 ∩ 渠道级交集）。"""
    env = channels_env
    env.bind(env.users.alice.user_id, "telegram", "10001")
    env.bind(env.users.alice.user_id, "wxpusher", "UID_WX")
    inst = _enabled_instance(env, env.users.alice)
    env.instance_service.update_notifications(
        env.users.alice, inst.user_strategy_id, {"fill": False}
    )

    outbox = env.make_outbox("directed")
    s = env.Session()
    try:
        outbox.enqueue(s, **_fill_kwargs(_owner_of(inst), "fill-instoff"))
        s.commit()
        assert _channels(outbox, "fill-instoff", session=s) == []
        assert outbox.event_exists("fill-instoff", session=s) is True
    finally:
        s.close()


def test_unmapped_instance_event_routes_on_instance_gate_only(channels_env) -> None:
    """无渠道开关键的实例事件（如 risk）：渠道级不适用（空约束恒真），仅实例级门控。

    裁决 1（规格复核）：计划第 7 条「实例级 9 类保持不变」是硬约束——
    plan/approval/risk/order/带实例 runtime_error 不得因无渠道开关键变死开关
    （渠道开关缺省全开与「缺开关=关」自相矛盾；无实例 runtime_error 走遗留
    全局开关的同款先例）。
    """
    env = channels_env
    env.bind(env.users.alice.user_id, "telegram", "10001")
    env.bind(env.users.alice.user_id, "wxpusher", "UID_WX")
    inst = _enabled_instance(env, env.users.alice)

    outbox = env.make_outbox("directed")
    s = env.Session()
    try:
        outbox.enqueue(
            s,
            event_id="risk-on",
            owner=_owner_of(inst),
            event_type="risk",
            payload={"risk": "max_drawdown"},
            occurred_at=_OCCURRED_AT,
        )
        s.commit()
        assert set(_channels(outbox, "risk-on", session=s)) == {"telegram", "wxpusher"}
    finally:
        s.close()


def test_unmapped_instance_event_blocked_by_instance_preference(channels_env) -> None:
    """无渠道开关键的实例事件：实例该键关（risk=False）→ 双渠道零 delivery。"""
    env = channels_env
    env.bind(env.users.alice.user_id, "telegram", "10001")
    env.bind(env.users.alice.user_id, "wxpusher", "UID_WX")
    inst = _enabled_instance(env, env.users.alice)
    env.instance_service.update_notifications(
        env.users.alice, inst.user_strategy_id, {"risk": False}
    )

    outbox = env.make_outbox("directed")
    s = env.Session()
    try:
        outbox.enqueue(
            s,
            event_id="risk-off",
            owner=_owner_of(inst),
            event_type="risk",
            payload={"risk": "max_drawdown"},
            occurred_at=_OCCURRED_AT,
        )
        s.commit()
        assert _channels(outbox, "risk-off", session=s) == []
        assert outbox.event_exists("risk-off", session=s) is True
    finally:
        s.close()


def test_report_requires_market_opt_in_per_channel(channels_env) -> None:
    """报告类市场 opt-in 关 → 无 delivery；开市场后照投（逐渠道独立偏好行）。"""
    from zhice_os_v2.application.services.notification_preference_service import (
        NotificationPreferenceBody,
    )

    env = channels_env
    env.bind(env.users.alice.user_id, "wxpusher", "UID_WX")
    owner = _no_instance_owner(env.users.alice.user_id)

    outbox = env.make_outbox("directed")
    s = env.Session()
    try:
        outbox.enqueue(
            s,
            event_id="mr-noopt",
            owner=owner,
            event_type="market_report",
            payload={"market": "US", "report_kind": "evening"},
            occurred_at=_OCCURRED_AT,
        )
        assert _channels(outbox, "mr-noopt", session=s) == []
        assert outbox.event_exists("mr-noopt", session=s) is True

        env.preference_service.update(
            env.users.alice.user_id,
            NotificationPreferenceBody(market_reports_enabled={"US": True}),
            channel="wxpusher",
        )
        outbox.enqueue(
            s,
            event_id="mr-opt",
            owner=owner,
            event_type="market_report",
            payload={"market": "US", "report_kind": "evening"},
            occurred_at=_OCCURRED_AT,
        )
        s.commit()
        assert _channels(outbox, "mr-opt", session=s) == ["wxpusher"]
    finally:
        s.close()


def test_report_morning_switch_gates_report_kind(channels_env) -> None:
    """morning_report_enabled=False + report_kind=morning → 无该渠道 delivery。"""
    from zhice_os_v2.application.services.notification_preference_service import (
        NotificationPreferenceBody,
    )

    env = channels_env
    env.bind(env.users.alice.user_id, "wxpusher", "UID_WX")
    env.preference_service.update(
        env.users.alice.user_id,
        NotificationPreferenceBody(
            market_reports_enabled={"US": True}, morning_report_enabled=False
        ),
        channel="wxpusher",
    )
    owner = _no_instance_owner(env.users.alice.user_id)

    outbox = env.make_outbox("directed")
    s = env.Session()
    try:
        outbox.enqueue(
            s,
            event_id="mr-morning",
            owner=owner,
            event_type="market_report",
            payload={"market": "US", "report_kind": "morning"},
            occurred_at=_OCCURRED_AT,
        )
        assert _channels(outbox, "mr-morning", session=s) == []

        outbox.enqueue(
            s,
            event_id="mr-evening",
            owner=owner,
            event_type="market_report",
            payload={"market": "US", "report_kind": "evening"},
            occurred_at=_OCCURRED_AT,
        )
        s.commit()
        assert _channels(outbox, "mr-evening", session=s) == ["wxpusher"]
    finally:
        s.close()


def test_system_alerts_off_on_telegram_row_blocks_all_channels(channels_env) -> None:
    """system_alerts 既有语义：读 telegram 行全局开关（非渠道级），关 → 双渠道都无。"""
    from zhice_os_v2.application.services.notification_preference_service import (
        NotificationPreferenceBody,
    )

    env = channels_env
    env.bind(env.users.alice.user_id, "telegram", "10001")
    env.bind(env.users.alice.user_id, "wxpusher", "UID_WX")
    owner = _no_instance_owner(env.users.alice.user_id)

    outbox = env.make_outbox("directed")
    s = env.Session()
    try:
        outbox.enqueue(
            s,
            event_id="re-on",
            owner=owner,
            event_type="runtime_error",
            payload={"source": "scheduler"},
            occurred_at=_OCCURRED_AT,
        )
        assert set(_channels(outbox, "re-on", session=s)) == {"telegram", "wxpusher"}

        env.preference_service.update(
            env.users.alice.user_id, NotificationPreferenceBody(system_alerts_enabled=False)
        )
        outbox.enqueue(
            s,
            event_id="re-off",
            owner=owner,
            event_type="runtime_error",
            payload={"source": "scheduler"},
            occurred_at=_OCCURRED_AT,
        )
        s.commit()
        assert _channels(outbox, "re-off", session=s) == []
        assert outbox.event_exists("re-off", session=s) is True
    finally:
        s.close()


def test_no_binding_at_all_writes_event_without_delivery(channels_env) -> None:
    """无任何渠道绑定 → 零 delivery、event 仍写（绑定硬门，防 worker 死行 churn）。"""
    env = channels_env
    inst = _enabled_instance(env, env.users.alice)

    outbox = env.make_outbox("directed")
    s = env.Session()
    try:
        outbox.enqueue(s, **_fill_kwargs(_owner_of(inst), "fill-nobind"))
        s.commit()
        assert _channels(outbox, "fill-nobind", session=s) == []
        assert outbox.event_exists("fill-nobind", session=s) is True
    finally:
        s.close()


def test_preference_query_failure_is_fail_closed(channels_env) -> None:
    """偏好查询异常 → 双渠道 fail-closed 零 delivery、event 仍写、不上抛。"""
    from zhice_os_v2.infrastructure.repositories.notification_preference_repository import (
        NotificationPreferenceRepository,
    )

    class _BrokenPreferenceRepository(NotificationPreferenceRepository):
        def get(self, user_id, channel="telegram"):  # type: ignore[no-untyped-def]
            raise RuntimeError("preference store down")

    env = channels_env
    env.bind(env.users.alice.user_id, "telegram", "10001")
    env.bind(env.users.alice.user_id, "wxpusher", "UID_WX")
    inst = _enabled_instance(env, env.users.alice)

    outbox = env.make_outbox(
        "directed", preference_repository=_BrokenPreferenceRepository()
    )
    s = env.Session()
    try:
        outbox.enqueue(s, **_fill_kwargs(_owner_of(inst), "fill-pqf"))
        s.commit()
        assert _channels(outbox, "fill-pqf", session=s) == []
        assert outbox.event_exists("fill-pqf", session=s) is True
    finally:
        s.close()
