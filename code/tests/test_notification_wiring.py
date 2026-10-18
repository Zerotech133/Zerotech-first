"""Phase C Task 8 接线测试：成交/报告三态通知路由（broadcast|shadow|directed）。

覆盖（计划 Task 8 + T7-M5 复核遗留）：
- ``route_fill``（真实 outbox/resolver/sqlite）：alice 成交只入 alice delivery；
  stop_loss/take_profit 映射；shadow event 无 delivery；directed+legacy 落
  ``notification.owner_missing`` 告警且绝不广播兜底；未绑定 chat_id → event 有
  delivery 无（T7-M5）；幂等；
- ``_record_fill`` 三态分发：broadcast 原路径一字不变（notify_fill 语义零回归）；
  shadow 新旧并行（owner 存在才并行广播）；directed 绝不广播；exit_kind 透传；
- 报告三态（``scheduler_jobs._push_report_telegram`` 唯一广播 chokepoint）：
  broadcast 原样；directed 不调 broadcast_telegram、收件人各一条 delivery（幂等）；
  shadow 原广播照发。
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

_NOW = datetime.now(UTC)


# ══════════════════════════════════════════════════════════════════════════════
# 隔离环境：偏好/账户/实例/用户仓储 + resolver + outbox own-session 同一 sqlite
# ══════════════════════════════════════════════════════════════════════════════
@pytest.fixture
def wiring_env(monkeypatch: pytest.MonkeyPatch, users):
    import zhice_os_v2.application.services.ownership_resolver as resolver_mod
    import zhice_os_v2.config.settings as settings_mod
    import zhice_os_v2.infrastructure.db.session as sess_mod
    import zhice_os_v2.infrastructure.repositories.notification_preference_repository as pref_repo_mod
    import zhice_os_v2.infrastructure.repositories.user_account_repository as acc_repo_mod
    import zhice_os_v2.infrastructure.repositories.user_repository as user_repo_mod
    import zhice_os_v2.infrastructure.repositories.user_strategy_repository as inst_repo_mod
    from zhice_os_v2.application.services.notification_preference_service import (
        NotificationPreferenceService,
    )
    from zhice_os_v2.application.services.user_account_service import UserAccountService
    from zhice_os_v2.application.services.user_strategy_service import UserStrategyService
    from zhice_os_v2.infrastructure.db.base import Base
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
    monkeypatch.setattr(resolver_mod, "get_session", lambda: Session())

    # 单点模式开关（notify_mode 与 outbox._mode 均现读 settings，不缓存）。
    fake_settings = SimpleNamespace(
        telegram_notification_mode="directed", persistence_backend="memory"
    )
    monkeypatch.setattr(settings_mod, "get_settings", lambda: fake_settings)

    from zhice_os_v2.infrastructure.db import models as orm

    def seed_user(user, chat_id: str | None = None) -> None:
        s = Session()
        try:
            s.add(
                orm.UserModel(
                    user_id=user.user_id,
                    username=user.username,
                    password_hash="x",
                    role="TRADER",
                    is_active=True,
                    locale="zh",
                    telegram_chat_id=chat_id,
                    created_at=_NOW,
                )
            )
            if chat_id:
                # Task 10 起 delivery 硬门 = v2_user_notification_binding（get_active）；
                # 已绑 chat 的用户同步种 active telegram 绑定行，维持
                # 「已绑定 = 可投递」前提（未绑 chat_id 仍不种 → 无 delivery）。
                s.add(
                    orm.UserNotificationBindingModel(
                        binding_id=f"nbind-wiring-{user.user_id}",
                        user_id=user.user_id,
                        channel="telegram",
                        recipient_id=chat_id,
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

    def seed_position(
        *,
        position_id: str,
        user_strategy_id: str | None,
        market: str = "US",
        ts_code: str = "AAPL",
        sv_id: str = "v1-us-sv-1",
    ) -> None:
        s = Session()
        try:
            s.add(
                orm.PositionModel(
                    position_id=position_id,
                    strategy_version_id=sv_id,
                    market=market,
                    ts_code=ts_code,
                    direction="LONG",
                    open_qty=Decimal("10"),
                    open_price=Decimal("100"),
                    status="OPEN",
                    opened_at=_NOW,
                    user_strategy_id=user_strategy_id,
                )
            )
            s.commit()
        finally:
            s.close()

    db = InMemoryDatabase()
    return SimpleNamespace(
        Session=Session,
        db=db,
        users=users,
        settings=fake_settings,
        seed_user=seed_user,
        seed_position=seed_position,
        instance_service=UserStrategyService(db=db, account_service=UserAccountService()),
        preference_service=NotificationPreferenceService(),
    )


def _enabled_instance(env, user, market: str = "US", *, name: str | None = None):
    """公共版本 → 账户实例 → 启用（fill 等实例事件与报告收件人的共同前提）。"""
    from zhice_os_v2.application.services.strategy_catalog_service import (
        StrategyCatalogService,
    )

    catalog = StrategyCatalogService(env.db)
    strat = catalog.create_public(name or f"pub-{market}", market=market)
    version = next(
        v for v in env.db.list_strategy_versions() if v.strategy_id == strat.strategy_id
    )
    inst = env.instance_service.create(
        user, version.strategy_version_id, initial_capital=10_000
    )
    return env.instance_service.enable(user, inst.user_strategy_id)


def _lot(env_position_id: str, user_strategy_id: str | None):
    from zhice_os_v2.domain.portfolio.position import PositionLot

    return PositionLot(
        position_id=env_position_id,
        strategy_version_id="v1-us-sv-1",
        market="US",
        ts_code="AAPL",
        direction="LONG",
        open_qty=Decimal("10"),
        open_price=Decimal("100"),
        user_strategy_id=user_strategy_id,
    )


def _outbox_reader(env):
    from zhice_os_v2.application.services.notification_outbox_service import (
        NotificationOutboxService,
    )

    return NotificationOutboxService()


def _event_rows(env, event_id: str | None = None):
    from zhice_os_v2.infrastructure.db import models as orm

    s = env.Session()
    try:
        q = s.query(orm.NotificationEventModel)
        if event_id is not None:
            q = q.filter_by(event_id=event_id)
        return q.all()
    finally:
        s.close()


def _fill_kwargs(**over) -> dict:
    kw = dict(
        fill_id="fill-w1",
        side="SELL",
        ts_code="AAPL",
        price=Decimal("101"),
        qty=Decimal("10"),
        amount=Decimal("1010"),
        market="US",
        sv_id="v1-us-sv-1",
        sell_reason="止损离场：跌破止损位，执行风控纪律",
        realized_pnl=Decimal("10"),
        fill_time=_NOW,
        position=_lot("pos-w1", None),
        exit_kind=None,
    )
    kw.update(over)
    return kw


# ══════════════════════════════════════════════════════════════════════════════
# Section 1：route_fill（真实 outbox + resolver + sqlite）
# ══════════════════════════════════════════════════════════════════════════════
def test_alice_fill_only_enqueues_alice_delivery(wiring_env) -> None:
    """directed：alice 实例的成交 → delivery 只含 alice 的 user_id；bob 无。"""
    env = wiring_env
    env.settings.telegram_notification_mode = "directed"
    alice_inst = _enabled_instance(env, env.users.alice, name="pub-alice")
    _enabled_instance(env, env.users.bob, name="pub-bob")
    env.seed_user(env.users.alice, chat_id="1001")
    env.seed_user(env.users.bob, chat_id="1002")
    env.seed_position(position_id="pos-w1", user_strategy_id=alice_inst.user_strategy_id)

    from zhice_os_v2.application.services.notification_routing import route_fill

    route_fill(**_fill_kwargs(position=_lot("pos-w1", alice_inst.user_strategy_id)))

    reader = _outbox_reader(env)
    deliveries = reader.deliveries_for("fill-w1")
    assert [d.user_id for d in deliveries] == [env.users.alice.user_id]
    assert deliveries[0].status == "PENDING"


def test_fill_exit_kind_maps_stop_loss_and_take_profit(wiring_env) -> None:
    """平仓按 exit_kind 映射 STOP_LOSS/TAKE_PROFIT 事件类型；未映射为 FILL。"""
    env = wiring_env
    env.settings.telegram_notification_mode = "directed"
    alice_inst = _enabled_instance(env, env.users.alice, name="pub-map")
    env.seed_user(env.users.alice, chat_id="1001")
    env.seed_position(position_id="pos-m1", user_strategy_id=alice_inst.user_strategy_id)

    from zhice_os_v2.application.services.notification_routing import route_fill

    route_fill(**_fill_kwargs(
        fill_id="fill-m1", exit_kind="STOP_LOSS",
        position=_lot("pos-m1", alice_inst.user_strategy_id),
    ))
    route_fill(**_fill_kwargs(
        fill_id="fill-m2", exit_kind="TAKE_PROFIT",
        position=_lot("pos-m1", alice_inst.user_strategy_id),
    ))
    route_fill(**_fill_kwargs(
        fill_id="fill-m3", exit_kind="TIME_STOP", side="SELL",
        position=_lot("pos-m1", alice_inst.user_strategy_id),
    ))
    route_fill(**_fill_kwargs(
        fill_id="fill-m4", side="BUY",
        position=_lot("pos-m1", alice_inst.user_strategy_id),
    ))

    types = {row.event_id: row.event_type for row in _event_rows(env)}
    assert types["fill-m1"] == "stop_loss"
    assert types["fill-m2"] == "take_profit"
    assert types["fill-m3"] == "fill"  # 其余离场类型归 FILL
    assert types["fill-m4"] == "fill"  # 建仓恒 FILL
    reader = _outbox_reader(env)
    for eid in ("fill-m1", "fill-m2", "fill-m3", "fill-m4"):
        assert reader.delivery_count(eid) == 1


def test_shadow_fill_writes_event_without_delivery(wiring_env) -> None:
    """shadow：event 落库、无 delivery（原广播是否照发由 _record_fill 分发测试覆盖）。"""
    env = wiring_env
    env.settings.telegram_notification_mode = "shadow"
    alice_inst = _enabled_instance(env, env.users.alice, name="pub-sh")
    env.seed_user(env.users.alice, chat_id="1001")
    env.seed_position(position_id="pos-sh", user_strategy_id=alice_inst.user_strategy_id)

    from zhice_os_v2.application.services.notification_routing import route_fill

    route_fill(**_fill_kwargs(position=_lot("pos-sh", alice_inst.user_strategy_id)))

    reader = _outbox_reader(env)
    assert reader.event_exists("fill-w1") is True
    assert reader.delivery_count("fill-w1") == 0


def test_directed_legacy_fill_alerts_owner_missing(
    wiring_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """directed + legacy 成交（无归属）：不广播、不写 event，落 owner_missing 告警。"""
    env = wiring_env
    env.settings.telegram_notification_mode = "directed"
    import zhice_os_v2.application.services.alert_service as alert_mod
    import zhice_os_v2.application.services.telegram_notifier as tn

    alerts: list[tuple] = []
    monkeypatch.setattr(
        alert_mod, "send_alert", lambda *a, **k: alerts.append((a, k)) or True
    )
    broadcasts: list[str] = []
    monkeypatch.setattr(
        tn, "broadcast_telegram", lambda text: broadcasts.append(text) or 1
    )

    from zhice_os_v2.application.services.notification_routing import route_fill

    route_fill(**_fill_kwargs(fill_id="fill-legacy-1", position=_lot("pos-none", None)))

    assert alerts, "owner 解析失败必须落系统告警"
    args, _ = alerts[0]
    assert args[0] == "WARN"
    assert args[1] == "notification.owner_missing"
    assert "fill-legacy-1" in args[2]
    assert broadcasts == []  # 绝不广播兜底
    assert _event_rows(env) == []  # 不写任何 event


def test_shadow_legacy_fill_logs_without_alert(
    wiring_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """shadow + legacy 成交：同样不广播不写 event，但只记日志、不落告警。"""
    env = wiring_env
    env.settings.telegram_notification_mode = "shadow"
    import zhice_os_v2.application.services.alert_service as alert_mod

    alerts: list[tuple] = []
    monkeypatch.setattr(
        alert_mod, "send_alert", lambda *a, **k: alerts.append((a, k)) or True
    )

    from zhice_os_v2.application.services.notification_routing import route_fill

    route_fill(**_fill_kwargs(fill_id="fill-legacy-2", position=_lot("pos-none", None)))
    assert alerts == []
    assert _event_rows(env) == []


def test_unbound_user_gets_event_without_delivery(wiring_env) -> None:
    """T7-M5：未绑定 chat_id 的用户 → event 有、delivery 无（防 no_chat_binding churn）。"""
    env = wiring_env
    env.settings.telegram_notification_mode = "directed"
    alice_inst = _enabled_instance(env, env.users.alice, name="pub-nochat")
    env.seed_user(env.users.alice, chat_id=None)  # 实例在、偏好默认开、但未绑定
    env.seed_position(position_id="pos-nc", user_strategy_id=alice_inst.user_strategy_id)

    from zhice_os_v2.application.services.notification_routing import route_fill

    route_fill(**_fill_kwargs(position=_lot("pos-nc", alice_inst.user_strategy_id)))

    reader = _outbox_reader(env)
    assert reader.event_exists("fill-w1") is True
    assert reader.delivery_count("fill-w1") == 0


def test_fill_enqueue_is_idempotent(wiring_env) -> None:
    """同 fill_id 二次路由：不新建 event/delivery（幂等锚点 = fill_id）。"""
    env = wiring_env
    env.settings.telegram_notification_mode = "directed"
    alice_inst = _enabled_instance(env, env.users.alice, name="pub-idem")
    env.seed_user(env.users.alice, chat_id="1001")
    env.seed_position(position_id="pos-id", user_strategy_id=alice_inst.user_strategy_id)

    from zhice_os_v2.application.services.notification_routing import route_fill

    kw = _fill_kwargs(position=_lot("pos-id", alice_inst.user_strategy_id))
    route_fill(**kw)
    route_fill(**kw)

    reader = _outbox_reader(env)
    assert reader.delivery_count("fill-w1") == 1
    assert len(_event_rows(env, "fill-w1")) == 1


# ══════════════════════════════════════════════════════════════════════════════
# Section 2：_record_fill 三态分发（FakeSession + mock route_fill/notify_fill）
# ══════════════════════════════════════════════════════════════════════════════
class _FakeSession:
    """吃掉四表 upsert 的假会话（只验证通知分发，不落库）。"""

    def __init__(self) -> None:
        self.executed: list[object] = []

    def execute(self, stmt) -> None:  # type: ignore[no-untyped-def]
        self.executed.append(stmt)

    def commit(self) -> None:
        pass

    def close(self) -> None:
        pass


@pytest.fixture
def dispatch_env(monkeypatch: pytest.MonkeyPatch):
    """_record_fill 分发环境：sql 假 settings + 假 session + route/notify 双记录器。"""
    import zhice_os_v2.application.services.notification_routing as routing_mod
    import zhice_os_v2.application.services.telegram_notifier as tn
    import zhice_os_v2.config.settings as settings_mod
    import zhice_os_v2.infrastructure.db.session as sess_mod

    fake_settings = SimpleNamespace(
        persistence_backend="sql", telegram_notification_mode="directed"
    )
    monkeypatch.setattr(settings_mod, "get_settings", lambda: fake_settings)
    fake_session = _FakeSession()
    monkeypatch.setattr(sess_mod, "get_session", lambda: fake_session)

    route_calls: list[tuple] = []
    notify_calls: list[dict] = []
    state = SimpleNamespace(owner=SimpleNamespace(user_id="u-alice"))

    def fake_route(session, **kw):  # type: ignore[no-untyped-def]
        route_calls.append((session, kw))
        return state.owner

    monkeypatch.setattr(routing_mod, "route_fill", fake_route)
    monkeypatch.setattr(tn, "notify_fill", lambda **kw: notify_calls.append(kw))
    return SimpleNamespace(
        settings=fake_settings,
        session=fake_session,
        route_calls=route_calls,
        notify_calls=notify_calls,
        state=state,
    )


def _record_buy(env) -> None:
    """BUY 成交（approval=None，position 带 sv）——_auto_execute 同形。"""
    from zhice_os_v2.application.services.workflow_service import WorkflowService
    from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase

    WorkflowService(InMemoryDatabase())._record_fill(
        market="CN",
        ts_code="600519.SH",
        side="BUY",
        qty=Decimal("100"),
        price=Decimal("1680"),
        position=_lot("pos-x1", None),
    )


def _record_sell(env, exit_kind: str | None = "STOP_LOSS") -> None:
    from zhice_os_v2.application.services.workflow_service import WorkflowService
    from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase

    WorkflowService(InMemoryDatabase())._record_fill(
        market="US",
        ts_code="AAPL",
        side="SELL",
        qty=Decimal("10"),
        price=Decimal("101"),
        position=_lot("pos-x1", None),
        sell_reason="止损离场",
        realized_pnl=Decimal("10"),
        exit_kind=exit_kind,
    )


def test_record_fill_broadcast_calls_notify_fill_only(dispatch_env) -> None:
    """broadcast：原 notify_fill 广播路径一字不变（参数原样），定向路由不进入。"""
    env = dispatch_env
    env.settings.telegram_notification_mode = "broadcast"
    _record_buy(env)
    assert len(env.notify_calls) == 1
    kw = env.notify_calls[0]
    assert kw["side"] == "BUY"
    assert kw["ts_code"] == "600519.SH"
    assert kw["market"] == "CN"
    assert kw["sv_id"] == "v1-us-sv-1"
    assert kw["price"] == Decimal("1680")
    assert kw["qty"] == Decimal("100")
    assert kw["amount"] == Decimal("168000")
    assert kw["reason"] is None
    assert kw["sell_reason"] is None
    assert kw["realized_pnl"] is None
    assert isinstance(kw["fill_time"], datetime)
    assert env.route_calls == []  # broadcast 不走定向分支


def test_record_fill_shadow_parallel_broadcast_when_owner(dispatch_env) -> None:
    """shadow + owner 已解析：原广播照发（新旧并行观察）+ 定向路由被调。"""
    env = dispatch_env
    env.settings.telegram_notification_mode = "shadow"
    _record_buy(env)
    assert len(env.route_calls) == 1
    assert len(env.notify_calls) == 1  # 原广播仍发


def test_record_fill_shadow_no_broadcast_when_owner_missing(dispatch_env) -> None:
    """shadow + legacy（owner 解析失败）：不广播（铁律：绝不广播兜底）。"""
    env = dispatch_env
    env.settings.telegram_notification_mode = "shadow"
    env.state.owner = None
    _record_buy(env)
    assert len(env.route_calls) == 1
    assert env.notify_calls == []


def test_record_fill_directed_never_broadcasts(dispatch_env) -> None:
    """directed：只走定向路由（exit_kind 透传），notify_fill 绝不被调。"""
    env = dispatch_env
    env.settings.telegram_notification_mode = "directed"
    _record_sell(env, exit_kind="STOP_LOSS")
    assert len(env.route_calls) == 1
    session, kw = env.route_calls[0]
    assert session is env.session  # 同事务 enqueue（fill 的写事务会话）
    assert kw["fill_id"] == "fill-close-pos-x1"
    assert kw["exit_kind"] == "STOP_LOSS"
    assert kw["side"] == "SELL"
    assert env.notify_calls == []


# ══════════════════════════════════════════════════════════════════════════════
# Section 2.5：enqueue 事务中毒防护（终审 Important #1，SAVEPOINT）
# ══════════════════════════════════════════════════════════════════════════════
def test_enqueue_statement_error_never_poisons_fill_transaction(
    wiring_env, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """终审 Important #1：enqueue 的 INSERT 语句级错绝不毒化外层成交事务。

    场景（_record_fill 同构：成交写在 session 上、尚未 commit，同一 session 传给
    route_fill → enqueue 的 delivery INSERT 在 DB 层报语句级错）。修复前：
    - PG：语句错把事务置 aborted，route_fill/_record_fill 的 try/except 吞得掉
      异常吞不掉事务态 → session.commit() 失败、**整笔成交丢失**；
    - sqlite（本测试）：无 aborted 态，commit 照过。

    修复演进：Phase C 终审用外层 SAVEPOINT 整体回滚（通知零残留）；Task 10 起
    delivery 写入按渠道各包独立 SAVEPOINT——单渠道 delivery 失败只丢该渠道
    delivery（本用例 telegram 是唯一 eligible 渠道 → delivery 零残留），event
    按契约保留（审计/shadow 对照要完整事件流），外层事务保持可提交、fill 落库。

    连接模型须与生产同构（每会话独立连接）：wiring_env 的 StaticPool 单连接下，
    enqueue 路由期开/关的只读会话（渠道偏好仓储 get 等）会把调用方在途事务一并
    回滚（测试态伪影；生产 PG 独立连接无此问题）——故本用例换 file sqlite。
    """
    env = wiring_env
    env.settings.telegram_notification_mode = "directed"

    import sqlalchemy as sa

    import zhice_os_v2.application.services.notification_outbox_service as outbox_mod
    import zhice_os_v2.application.services.ownership_resolver as resolver_mod
    import zhice_os_v2.infrastructure.db.session as sess_mod
    import zhice_os_v2.infrastructure.repositories.notification_preference_repository as pref_repo_mod
    import zhice_os_v2.infrastructure.repositories.user_account_repository as acc_repo_mod
    import zhice_os_v2.infrastructure.repositories.user_repository as user_repo_mod
    import zhice_os_v2.infrastructure.repositories.user_strategy_repository as inst_repo_mod
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.base import Base

    # file sqlite + 默认连接池：每会话独立连接（生产同构），savepoint 语义不变
    engine = create_engine(f"sqlite:///{tmp_path}/savepoint.db")
    Base.metadata.create_all(engine)
    FileSession = sessionmaker(bind=engine, expire_on_commit=False)
    for mod in (pref_repo_mod, acc_repo_mod, inst_repo_mod, user_repo_mod, sess_mod):
        monkeypatch.setattr(mod, "get_session", lambda: FileSession())
    monkeypatch.setattr(resolver_mod, "get_session", lambda: FileSession())

    alice_inst = _enabled_instance(env, env.users.alice, name="pub-sp")

    def _seed(chat_id: str | None) -> None:
        s = FileSession()
        try:
            s.add(orm.UserModel(
                user_id=env.users.alice.user_id, username=env.users.alice.username,
                password_hash="x", role="TRADER", is_active=True, locale="zh",
                telegram_chat_id=chat_id, created_at=_NOW,
            ))
            if chat_id:
                # Task 10：delivery 硬门走 v2_user_notification_binding → 同步种绑定
                s.add(orm.UserNotificationBindingModel(
                    binding_id="nbind-savepoint-telegram",
                    user_id=env.users.alice.user_id, channel="telegram",
                    recipient_id=chat_id, status="active", bound_at=_NOW,
                    revoked_at=None, created_at=_NOW, updated_at=_NOW,
                ))
            s.add(orm.PositionModel(
                position_id="pos-sp", strategy_version_id="v1-us-sv-1",
                market="US", ts_code="AAPL", direction="LONG",
                open_qty=Decimal("10"), open_price=Decimal("100"),
                status="OPEN", opened_at=_NOW,
                user_strategy_id=alice_inst.user_strategy_id,
            ))
            s.commit()
        finally:
            s.close()

    _seed("1001")  # 已绑定（chat + telegram 绑定行）→ 绑定门过、delivery 会被写（失败注入点）

    # delivery INSERT 抛真实语句级 DB 错（sqlite/PG 都炸、且留在事务内）；
    # 纯 Python raise 毒不到事务，测不出 savepoint。event INSERT 走原实现
    # （成功写入——正是要被 savepoint 回滚掉的"通知半行"）。
    _original_insert = outbox_mod.NotificationOutboxService._insert_on_conflict_do_nothing

    def _failing_delivery_insert(session, model, values, *, index_elements):
        if model is orm.NotificationDeliveryModel:
            session.execute(sa.text("INSERT INTO v2_no_such_table_for_test (id) VALUES (1)"))
            raise AssertionError("unreachable: 上一行必然抛 DB 语句错")
        return _original_insert(session, model, values, index_elements=index_elements)

    monkeypatch.setattr(
        outbox_mod.NotificationOutboxService,
        "_insert_on_conflict_do_nothing",
        staticmethod(_failing_delivery_insert),
    )

    from zhice_os_v2.application.services.notification_routing import route_fill

    # ── 调用方事务（_record_fill 同构）：成交写在途 → 同 session enqueue → commit ──
    fill_id = "fill-close-pos-sp"
    session = FileSession()
    try:
        session.add(
            orm.FillRecordModel(
                id=1,  # BigInteger PK 在 sqlite 非 rowid 别名，显式给值
                fill_id=fill_id,
                execution_id="exec-close-pos-sp",
                market="US",
                ts_code="AAPL",
                broker_trade_id="SIM-close-pos-sp",
                fill_price=Decimal("101"),
                fill_qty=Decimal("10"),
                fill_amount=Decimal("1010"),
                fill_time=_NOW,
                realized_pnl=Decimal("10"),
                sell_reason="离场",
                created_at=_NOW,
            )
        )
        session.flush()  # 成交行已在事务内（未 commit）

        route_fill(
            session,
            fill_id=fill_id,
            side="SELL",
            ts_code="AAPL",
            price=Decimal("101"),
            qty=Decimal("10"),
            amount=Decimal("1010"),
            market="US",
            sv_id="v1-us-sv-1",
            sell_reason="离场",
            realized_pnl=Decimal("10"),
            fill_time=_NOW,
            exit_kind=None,
            position=_lot("pos-sp", alice_inst.user_strategy_id),
        )
        session.commit()  # 修复前 sqlite 可过但残留 event；PG 上直接失败
    finally:
        session.close()

    s = FileSession()
    try:
        fill = s.query(orm.FillRecordModel).filter_by(fill_id=fill_id).one_or_none()
        assert fill is not None, "enqueue 失败后成交必须照常落库"
        # Task 10 逐渠道 SAVEPOINT：delivery INSERT 失败只丢该渠道 delivery（此处
        # telegram 是唯一 eligible 渠道 → delivery 零残留）；event 按契约保留
        # （事件流是审计/shadow 对照的完整记录），外层成交事务不受任何污染。
        assert s.query(orm.NotificationEventModel).count() == 1, "event 按契约保留"
        assert s.query(orm.NotificationDeliveryModel).count() == 0, "失败渠道 delivery 零残留"
    finally:
        s.close()


# ══════════════════════════════════════════════════════════════════════════════
# Section 3：报告三态（_push_report_telegram 唯一广播 chokepoint）
# ══════════════════════════════════════════════════════════════════════════════
_REPORT_R = {"narrative": "美股晚报正文", "report_date": "2026-09-07"}


def _patch_broadcast(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    import zhice_os_v2.application.services.telegram_notifier as tn

    calls: list[str] = []
    monkeypatch.setattr(tn, "broadcast_telegram", lambda text: calls.append(text) or 1)
    return calls


def _push(market: str = "US", label: str = "晚报") -> None:
    from zhice_os_v2 import scheduler_jobs

    scheduler_jobs._push_report_telegram(
        market, label, _REPORT_R, report_kind="evening"
    )


def _report_event_id(uid: str, kind: str = "evening") -> str:
    from datetime import date

    from zhice_os_v2.application.services.notification_routing import (
        ReportKind,
        report_event_id,
    )

    return report_event_id("US", date(2026, 9, 7), ReportKind(kind), uid)


def test_report_broadcast_unchanged(wiring_env, monkeypatch: pytest.MonkeyPatch) -> None:
    """broadcast：broadcast_telegram 原样调用（格式一字不变），outbox 零写入。"""
    env = wiring_env
    env.settings.telegram_notification_mode = "broadcast"
    calls = _patch_broadcast(monkeypatch)
    _push()
    assert calls == ["📊 <b>US 晚报</b>\n\n美股晚报正文"]
    assert _event_rows(env) == []


def test_report_directed_per_recipient_delivery(
    wiring_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """directed：不调 broadcast_telegram；recipients 各一条 delivery；同日重跑幂等。"""
    env = wiring_env
    env.settings.telegram_notification_mode = "directed"
    calls = _patch_broadcast(monkeypatch)
    _enabled_instance(env, env.users.alice, name="pub-r1")
    _enabled_instance(env, env.users.bob, name="pub-r2")
    env.preference_service.set_market_report(env.users.alice, "US", True)
    env.preference_service.set_market_report(env.users.bob, "US", True)
    env.seed_user(env.users.alice, chat_id="2001")
    env.seed_user(env.users.bob, chat_id="2002")

    _push()
    _push()  # 同日重跑：幂等

    assert calls == []
    reader = _outbox_reader(env)
    expected = {
        _report_event_id(env.users.alice.user_id): env.users.alice.user_id,
        _report_event_id(env.users.bob.user_id): env.users.bob.user_id,
    }
    for event_id, uid in expected.items():
        deliveries = reader.deliveries_for(event_id)
        assert [d.user_id for d in deliveries] == [uid]
        assert deliveries[0].status == "PENDING"
    assert len(_event_rows(env)) == 2  # 幂等：无新增事件
    payload = _event_rows(env, _report_event_id(env.users.alice.user_id))[0]
    assert payload.event_type == "market_report"
    assert payload.payload["market"] == "US"
    assert payload.payload["narrative"] == "美股晚报正文"
    assert payload.payload["report_kind"] == "evening"  # Task 11：kind 贯穿 payload


def test_report_directed_no_recipients_no_rows(
    wiring_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """directed 无收件人：跳过（不广播、零写入）。"""
    env = wiring_env
    env.settings.telegram_notification_mode = "directed"
    calls = _patch_broadcast(monkeypatch)
    _push()
    assert calls == []
    assert _event_rows(env) == []


def test_report_shadow_parallel(wiring_env, monkeypatch: pytest.MonkeyPatch) -> None:
    """shadow：原广播照发 + 收件人定向计算入 outbox（event 有、delivery 无）。"""
    env = wiring_env
    env.settings.telegram_notification_mode = "shadow"
    calls = _patch_broadcast(monkeypatch)
    _enabled_instance(env, env.users.alice, name="pub-r3")
    env.preference_service.set_market_report(env.users.alice, "US", True)
    env.seed_user(env.users.alice, chat_id="2003")

    _push()

    assert calls == ["📊 <b>US 晚报</b>\n\n美股晚报正文"]
    reader = _outbox_reader(env)
    event_id = _report_event_id(env.users.alice.user_id)
    assert reader.event_exists(event_id) is True
    assert reader.delivery_count(event_id) == 0
