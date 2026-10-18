"""Task 11 业务接线：晨晚报 ReportKind 幂等键 + 按渠道收件人并集 + 调度门。

覆盖（契约最小测试集 + 升级兼容）：
- ``ReportKind`` / ``report_event_id``：晨晚报同日 event_id 不同且 ≤64；同类重跑
  幂等（event 计数 1）；
- ``route_market_report(report_kind=...)``：收件人 = telegram/wxpusher 两渠道
  ``report_recipients`` 并集，逐用户只 enqueue 一个 event（渠道由 outbox 按
  active bindings 决定）——wxpusher-only 订阅者不被 Telegram 口径过滤掉；
  双渠道用户 1 event / 2 delivery；晨开晚关用户只收晨报；
- payload 必含 ``report_kind``，且不泄露 qty/amount/cost/realized_pnl；
- 升级兼容：当日旧 key ``mreport-{market}-{iso}-{uid}`` 已存在 → 同用户同日
  新 key 不补发（防升级重发）；
- ownerless fill 双渠道零发送（铁律：绝不广播兜底）；
- 调度门：``run_notification_worker_job`` 生产装配 transports/binding_repository/
  channel_enabled（wxpusher 暂停只停该渠道 claim，telegram 恒可投）；晨/晚报
  job 贯穿 ``report_kind``。
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
_REPORT_DATE = "2026-09-17"


# ══════════════════════════════════════════════════════════════════════════════
# 隔离环境：偏好/账户/实例/用户仓储 + resolver + outbox own-session 同一 sqlite
# ══════════════════════════════════════════════════════════════════════════════
@pytest.fixture
def routing_env(monkeypatch: pytest.MonkeyPatch, users):
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

    fake_settings = SimpleNamespace(
        telegram_notification_mode="directed", persistence_backend="memory"
    )
    monkeypatch.setattr(settings_mod, "get_settings", lambda: fake_settings)

    from zhice_os_v2.infrastructure.db import models as orm

    def seed_user(user) -> None:
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
                    created_at=_NOW,
                )
            )
            s.commit()
        finally:
            s.close()

    def bind(user_id: str, channel: str, recipient_id: str) -> None:
        s = Session()
        try:
            s.add(
                orm.UserNotificationBindingModel(
                    binding_id=f"nbind-t11-{channel}-{user_id}",
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
    return SimpleNamespace(
        Session=Session,
        db=db,
        users=users,
        settings=fake_settings,
        seed_user=seed_user,
        bind=bind,
        instance_service=UserStrategyService(db=db, account_service=UserAccountService()),
        preference_service=NotificationPreferenceService(),
    )


def _enabled_instance(env, user, market: str = "US", *, name: str | None = None):
    """公共版本 → 账户实例 → 启用（报告收件人 = 启用实例 ∩ 偏好 的前提）。"""
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


def _subscribe_channel(env, user, channel: str, *, market: str = "US") -> None:
    """渠道订阅三件套：active 绑定 + 市场 opt-in（kind 开关用缺省开）。"""
    from zhice_os_v2.application.services.notification_preference_service import (
        NotificationPreferenceBody,
    )

    env.bind(user.user_id, channel, f"rcpt-{channel}-{user.username}")
    env.preference_service.update(
        user.user_id,
        NotificationPreferenceBody(market_reports_enabled={market: True}),
        channel=channel,
    )


def _route_report(env, kind: str, *, market: str = "US", label: str = "晨报") -> None:
    from zhice_os_v2.application.services.notification_routing import route_market_report

    route_market_report(
        market=market,
        label=label,
        narrative=f"{market} {label}正文",
        report_kind=kind,
        report_date=_REPORT_DATE,
    )


def _event_ids(env) -> list[str]:
    from zhice_os_v2.infrastructure.db import models as orm

    s = env.Session()
    try:
        return [r.event_id for r in s.query(orm.NotificationEventModel).all()]
    finally:
        s.close()


def _event_row(env, event_id: str):
    from zhice_os_v2.infrastructure.db import models as orm

    s = env.Session()
    try:
        return (
            s.query(orm.NotificationEventModel)
            .filter_by(event_id=event_id)
            .one_or_none()
        )
    finally:
        s.close()


def _delivery_channels(env, event_id: str) -> list[str]:
    from zhice_os_v2.application.services.notification_outbox_service import (
        NotificationOutboxService,
    )

    return [d.channel for d in NotificationOutboxService().deliveries_for(event_id)]


def _expected_event_id(env, uid: str, kind: str) -> str:
    from datetime import date

    from zhice_os_v2.application.services.notification_routing import (
        ReportKind,
        report_event_id,
    )

    return report_event_id("US", date(2026, 9, 17), ReportKind(kind), uid)


# ══════════════════════════════════════════════════════════════════════════════
# 契约最小测试集：幂等键 / 渠道并集 / 升级兼容
# ══════════════════════════════════════════════════════════════════════════════
def test_wxpusher_only_report_subscriber_is_not_filtered_out(routing_env) -> None:
    """wxpusher-only 用户（市场订阅 + morning 开）→ 只产出 wxpusher delivery。

    修复前口径：report_recipients(market) 只按 Telegram 收件人算 → wxpusher-only
    订阅者被整体过滤掉（收不到任何晨晚报）。
    """
    env = routing_env
    alice = env.users.alice
    env.seed_user(alice)
    _enabled_instance(env, alice, name="pub-t11-wx")
    _subscribe_channel(env, alice, "wxpusher")

    _route_report(env, "morning")

    event_id = _expected_event_id(env, alice.user_id, "morning")
    assert _delivery_channels(env, event_id) == ["wxpusher"]
    assert _event_row(env, event_id) is not None


def test_morning_and_evening_have_distinct_idempotency_keys(routing_env) -> None:
    """同日晨晚 event_id 不同且 ≤64；各自独立入队（互不幂等吞并）。"""
    env = routing_env
    alice = env.users.alice
    env.seed_user(alice)
    _enabled_instance(env, alice, name="pub-t11-ke")
    _subscribe_channel(env, alice, "wxpusher")

    _route_report(env, "morning")
    _route_report(env, "evening", label="晚报")

    morning_id = _expected_event_id(env, alice.user_id, "morning")
    evening_id = _expected_event_id(env, alice.user_id, "evening")
    assert morning_id != evening_id
    assert len(morning_id) <= 64
    assert len(evening_id) <= 64
    assert morning_id in _event_ids(env)
    assert evening_id in _event_ids(env)


def test_same_report_kind_rerun_is_idempotent(routing_env) -> None:
    """同类重跑：event 计数 1、delivery 不叠加（唯一键幂等沿用现有机制）。"""
    env = routing_env
    alice = env.users.alice
    env.seed_user(alice)
    _enabled_instance(env, alice, name="pub-t11-idem")
    _subscribe_channel(env, alice, "wxpusher")

    _route_report(env, "morning")
    _route_report(env, "morning")  # 同日同类重跑（调度重试/重启场景）

    event_id = _expected_event_id(env, alice.user_id, "morning")
    assert _event_ids(env).count(event_id) == 1
    assert len(_delivery_channels(env, event_id)) == 1


def test_legacy_key_blocks_new_key_on_upgrade_day(routing_env) -> None:
    """升级兼容：当日旧 key mreport-{market}-{iso}-{uid} 已存在 → 新 key 不补发。"""
    env = routing_env
    alice, bob = env.users.alice, env.users.bob
    for user in (alice, bob):
        env.seed_user(user)
        _enabled_instance(env, user, name=f"pub-t11-leg-{user.username}")
        _subscribe_channel(env, user, "telegram")
    # 模拟升级前旧版已为 alice 投过当日晨报（旧幂等键事件行）
    legacy_id = f"mreport-US-{_REPORT_DATE}-{alice.user_id}"
    from zhice_os_v2.infrastructure.db import models as orm

    s = env.Session()
    try:
        s.add(
            orm.NotificationEventModel(
                event_id=legacy_id,
                user_id=alice.user_id,
                event_type="market_report",
                payload={"market": "US"},
                occurred_at=_NOW,
                created_at=_NOW,
            )
        )
        s.commit()
    finally:
        s.close()

    _route_report(env, "morning")

    assert _expected_event_id(env, alice.user_id, "morning") not in _event_ids(env), (
        "旧 key 当日已投 → 新 key 不得补发（防升级重发）"
    )
    assert _expected_event_id(env, bob.user_id, "morning") in _event_ids(env), (
        "无旧 key 的用户照常入队"
    )


def test_morning_on_evening_off_user_receives_only_morning(routing_env) -> None:
    """晨开晚关用户：只收晨报（晚报不入队该用户的事件）。"""
    env = routing_env
    alice = env.users.alice
    env.seed_user(alice)
    _enabled_instance(env, alice, name="pub-t11-me")
    _subscribe_channel(env, alice, "wxpusher")
    from zhice_os_v2.application.services.notification_preference_service import (
        NotificationPreferenceBody,
    )

    env.preference_service.update(
        alice.user_id,
        NotificationPreferenceBody(evening_report_enabled=False),
        channel="wxpusher",
    )

    _route_report(env, "morning")
    _route_report(env, "evening", label="晚报")

    assert _expected_event_id(env, alice.user_id, "morning") in _event_ids(env)
    assert _expected_event_id(env, alice.user_id, "evening") not in _event_ids(env)


def test_dual_channel_user_gets_one_event_two_deliveries(routing_env) -> None:
    """双渠道用户：报告 event 只 1 个、delivery 2 条（渠道由 outbox 按绑定决定）。"""
    env = routing_env
    alice = env.users.alice
    env.seed_user(alice)
    _enabled_instance(env, alice, name="pub-t11-dual")
    _subscribe_channel(env, alice, "telegram")
    _subscribe_channel(env, alice, "wxpusher")

    _route_report(env, "morning")

    event_id = _expected_event_id(env, alice.user_id, "morning")
    assert [e for e in _event_ids(env) if "morning" in e] == [event_id]
    assert sorted(_delivery_channels(env, event_id)) == ["telegram", "wxpusher"]


def test_report_payload_has_report_kind_and_no_forbidden_fields(routing_env) -> None:
    """payload 必含 report_kind；合规字段不泄露（qty/amount/cost/realized_pnl）。"""
    env = routing_env
    alice = env.users.alice
    env.seed_user(alice)
    _enabled_instance(env, alice, name="pub-t11-pay")
    _subscribe_channel(env, alice, "wxpusher")

    _route_report(env, "morning")

    row = _event_row(env, _expected_event_id(env, alice.user_id, "morning"))
    assert row is not None
    assert row.payload["report_kind"] == "morning"
    assert row.payload["market"] == "US"
    assert row.payload["date"] == _REPORT_DATE
    assert row.payload["narrative"]
    for forbidden in ("qty", "amount", "cost", "realized_pnl"):
        assert forbidden not in row.payload, f"报告 payload 泄露了 {forbidden}"


def test_missing_owner_never_broadcasts_on_either_channel(
    routing_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ownerless fill：两渠道零发送（不写 event、不广播——铁律：绝不广播兜底）。"""
    env = routing_env
    alice = env.users.alice
    env.seed_user(alice)
    _enabled_instance(env, alice, name="pub-t11-nobc")
    _subscribe_channel(env, alice, "telegram")
    _subscribe_channel(env, alice, "wxpusher")
    import zhice_os_v2.application.services.alert_service as alert_mod
    import zhice_os_v2.application.services.telegram_notifier as tn

    monkeypatch.setattr(alert_mod, "send_alert", lambda *a, **k: True)
    broadcasts: list[str] = []
    monkeypatch.setattr(
        tn, "broadcast_telegram", lambda text: broadcasts.append(text) or 1
    )

    from zhice_os_v2.application.services.notification_routing import route_fill
    from zhice_os_v2.domain.portfolio.position import PositionLot

    route_fill(
        fill_id="fill-t11-legacy",
        side="BUY",
        ts_code="AAPL",
        price=Decimal("101"),
        qty=Decimal("10"),
        amount=Decimal("1010"),
        market="US",
        sv_id="v1-us-sv-1",
        fill_time=_NOW,
        position=PositionLot(
            position_id="pos-t11-none",
            strategy_version_id="v1-us-sv-1",
            market="US",
            ts_code="AAPL",
            direction="LONG",
            open_qty=Decimal("10"),
            open_price=Decimal("100"),
            user_strategy_id=None,
        ),
    )

    assert broadcasts == []
    assert _event_ids(env) == []  # 无 event ⇒ 两渠道均无 delivery 可言


# ══════════════════════════════════════════════════════════════════════════════
# 调度门：run_notification_worker_job 生产装配 + 晨/晚报 report_kind 贯穿
# ══════════════════════════════════════════════════════════════════════════════
def _worker_settings(**over) -> SimpleNamespace:
    settings = SimpleNamespace(
        persistence_backend="sql",
        telegram_notification_mode="directed",
        wxpusher_delivery_enabled=False,
        wxpusher_app_token="tok-t11",
        wxpusher_timeout_seconds=8.0,
        notification_worker_batch_size=10,
        notification_retry_max=3,
    )
    for key, value in over.items():
        setattr(settings, key, value)
    return settings


def test_run_notification_worker_job_builds_channel_aware_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """scheduler_jobs 生产装配语义：transports 双渠道 + binding 仓储 + 渠道暂停门。

    wxpusher delivery_enabled=False 只暂停该渠道 claim（channel_enabled("wxpusher")
    为 False），telegram 恒 True（不因 WxPusher 暂停连带停摆）。
    """
    import zhice_os_v2.application.services.notification_worker as worker_mod
    import zhice_os_v2.config.settings as settings_mod
    import zhice_os_v2.scheduler_jobs as scheduler_jobs
    from zhice_os_v2.infrastructure.repositories.notification_binding_repository import (
        NotificationBindingRepository,
    )

    settings = _worker_settings()
    monkeypatch.setattr(settings_mod, "get_settings", lambda: settings)

    captured: dict = {}

    class _CapturingWorker:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def run_once(self) -> dict:
            return {"claimed": 0, "sent": 0, "retried": 0, "failed": 0, "recovered": 0}

    monkeypatch.setattr(worker_mod, "NotificationWorker", _CapturingWorker)

    stats = scheduler_jobs.run_notification_worker_job()

    assert stats["claimed"] == 0
    assert set(captured["transports"]) == {"telegram", "wxpusher"}
    assert isinstance(captured["binding_repository"], NotificationBindingRepository)
    enabled = captured["channel_enabled"]
    assert enabled("telegram") is True
    assert enabled("wxpusher") is False

    # 逐次现读：job 每轮重建 worker → 下一轮立即感知渠道启用
    settings.wxpusher_delivery_enabled = True
    scheduler_jobs.run_notification_worker_job()
    assert captured["channel_enabled"]("wxpusher") is True


def test_build_notification_worker_assembly_is_valid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """生产装配可被真实 NotificationWorker 构造接受（registry 键 ⊆ SUPPORTED）。"""
    import zhice_os_v2.config.settings as settings_mod
    import zhice_os_v2.scheduler_jobs as scheduler_jobs
    from zhice_os_v2.application.services.notification_worker import NotificationWorker
    from zhice_os_v2.infrastructure.notifications import (
        TelegramTransport,
        WxPusherTransport,
    )

    monkeypatch.setattr(settings_mod, "get_settings", lambda: _worker_settings())
    worker = scheduler_jobs._build_notification_worker()
    assert isinstance(worker, NotificationWorker)
    assert isinstance(worker._transports["telegram"], TelegramTransport)
    assert isinstance(worker._transports["wxpusher"], WxPusherTransport)


def test_morning_and_evening_jobs_pass_report_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """晨报 job → report_kind="morning"、晚报 → "evening"，贯穿 _push_report。"""
    import zhice_os_v2.api.routers.reports as reports_mod
    import zhice_os_v2.scheduler_jobs as scheduler_jobs

    pushed: list[dict] = []
    monkeypatch.setattr(
        scheduler_jobs,
        "_push_report_telegram",
        lambda market, label, r, report_kind: pushed.append(
            {"market": market, "label": label, "report_kind": report_kind}
        ),
    )
    monkeypatch.setattr(
        reports_mod,
        "compute_and_persist",
        lambda market, kind: {
            "narrative": "正文",
            "report_date": _REPORT_DATE,
            "model_used": "m",
            "source": "s",
        },
    )

    scheduler_jobs.run_morning_report_job("US")
    scheduler_jobs.run_evening_report_job("US")

    assert pushed == [
        {"market": "US", "label": "晨报", "report_kind": "morning"},
        {"market": "US", "label": "晚报", "report_kind": "evening"},
    ]


# ══════════════════════════════════════════════════════════════════════════════
# 复核追加（I-1/S-1/S-2）
# ══════════════════════════════════════════════════════════════════════════════
def test_worker_transports_are_reused_across_rounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I-1：transports 进程级缓存——两轮 job 复用同一 transport 实例。

    WxPusherTransport 持有长活 httpx.Client；每 15s 轮新建即每轮重付 TLS 握手与
    SSLContext 构造且旧 Client 依赖清洗。缓存后 identity 复用；channel_enabled
    仍逐轮现读（暂停/恢复语义不受缓存影响，同测试后半段证明）。
    """
    import zhice_os_v2.application.services.notification_worker as worker_mod
    import zhice_os_v2.config.settings as settings_mod
    import zhice_os_v2.scheduler_jobs as scheduler_jobs

    settings = _worker_settings()
    monkeypatch.setattr(settings_mod, "get_settings", lambda: settings)

    built: list[dict] = []

    class _CapturingWorker:
        def __init__(self, **kwargs) -> None:
            built.append(kwargs)

        def run_once(self) -> dict:
            return {"claimed": 0, "sent": 0, "retried": 0, "failed": 0, "recovered": 0}

    monkeypatch.setattr(worker_mod, "NotificationWorker", _CapturingWorker)

    scheduler_jobs.run_notification_worker_job()
    scheduler_jobs.run_notification_worker_job()

    assert len(built) == 2
    assert built[0]["transports"]["wxpusher"] is built[1]["transports"]["wxpusher"]
    assert built[0]["transports"]["telegram"] is built[1]["transports"]["telegram"]

    # channel_enabled 不经缓存：settings 变更下一轮即生效
    settings.wxpusher_delivery_enabled = True
    scheduler_jobs.run_notification_worker_job()
    assert built[2]["channel_enabled"]("wxpusher") is True


def test_unknown_report_kind_warns_and_skips(
    routing_env, caplog: pytest.LogCaptureFixture
) -> None:
    """S-1：未知 report_kind → 不投递且落 warning（含 market/kind，可追踪）。"""
    import logging

    env = routing_env
    alice = env.users.alice
    env.seed_user(alice)
    _enabled_instance(env, alice, name="pub-t11-badkind")
    _subscribe_channel(env, alice, "telegram")

    with caplog.at_level(logging.WARNING, logger="zhice_os_v2.application.services.notification_routing"):
        _route_report(env, "daily")

    assert _event_ids(env) == []  # fail-closed：整场不投
    warnings = [r for r in caplog.records if "market_report_unknown_kind" in r.message]
    assert warnings, "未知 kind 必须留 warning（否则报告无痕蒸发）"
    msg = warnings[0].getMessage()
    assert "market=US" in msg
    assert "report_kind=daily" in msg


def test_resolve_report_date_falls_back_to_today() -> None:
    """S-2（现行行为特征测试）：None / 非规范串 → 回退当天（UTC）。"""
    from datetime import UTC, datetime

    from zhice_os_v2.application.services.notification_routing import (
        _resolve_report_date,
    )

    today = datetime.now(UTC).date()
    assert _resolve_report_date(None) == today
    assert _resolve_report_date("2026-9-17") == today  # 非零填充 ISO → ValueError → 回退
    assert str(_resolve_report_date("2026-09-17")) == "2026-09-17"  # 规范串不回退
