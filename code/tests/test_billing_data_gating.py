"""P2 数据门禁：订阅者内置策略信号扇出 + payload 脱敏（无持仓/盈亏）。

隔离内存 sqlite；directed 模式下内置策略成交 → 活跃订阅者入 outbox（event+delivery），
payload 只含信号要素。核心断言：合规红线——订阅信号绝不含 qty/amount/realized_pnl。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest


@pytest.fixture()
def signal_env(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import zhice_os_v2.infrastructure.db.session as sess_mod
    import zhice_os_v2.infrastructure.repositories.membership_repository as mem_mod
    import zhice_os_v2.infrastructure.repositories.notification_preference_repository as pref_mod
    import zhice_os_v2.infrastructure.repositories.user_repository as user_mod
    from zhice_os_v2.config.settings import get_settings
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.base import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(mem_mod, "get_session", lambda: Session())
    monkeypatch.setattr(user_mod, "get_session", lambda: Session())
    # Task 10：订阅 signal 路由现读渠道级偏好（signal_enabled）且 C-1 起绑定读
    # 走 _own_session 自开短命会话 → 偏好仓储与 session 工厂都须指向隔离 sqlite
    # （同 wiring_env 五模块 patch 范式，防 _guard_real_pg 守卫拦截）。
    monkeypatch.setattr(pref_mod, "get_session", lambda: Session())
    monkeypatch.setattr(sess_mod, "get_session", lambda: Session())
    # directed 模式（订阅信号入 outbox event+delivery）
    monkeypatch.setattr(get_settings(), "telegram_notification_mode", "directed")

    now = datetime.now(UTC)
    s = Session()
    # 订阅者（已绑 Telegram：Task 10 起 delivery 硬门 = v2_user_notification_binding
    # 的 active 绑定行，不再读 user.telegram_chat_id —— 同 test_notification_wiring
    # seed_user 修法，种绑定行维持「已绑定 = 可投递」前提）
    s.add(
        orm.UserModel(
            user_id="u-sub", username="sub", password_hash="x", role="USER",
            is_active=True, locale="zh", telegram_chat_id="10086", created_at=now,
        )
    )
    s.add(
        orm.UserNotificationBindingModel(
            binding_id="nbind-billing-u-sub-telegram",
            user_id="u-sub",
            channel="telegram",
            recipient_id="10086",
            status="active",
            bound_at=now,
            revoked_at=None,
            created_at=now,
            updated_at=now,
        )
    )
    # 内置策略（owner_user_id=None）+ 版本
    s.add(
        orm.StrategyModel(
            strategy_id="strat-builtin", strategy_name="内置A", market="CRYPTO",
            strategy_type="V2_EXTERNAL", status="ACTIVE", description="",
            owner_user_id=None, visibility="PUBLIC", publication_status="PUBLISHED",
            created_at=now, updated_at=now,
        )
    )
    s.add(
        orm.StrategyVersionModel(
            strategy_version_id="sv-1", strategy_id="strat-builtin", version_no="1",
            market="CRYPTO", status="ACTIVE", spec={}, research_ref=None, created_at=now,
        )
    )
    # 一个用户自建策略（owner 非空）——用于验证不扇出
    s.add(
        orm.StrategyModel(
            strategy_id="strat-own", strategy_name="自建B", market="CRYPTO",
            strategy_type="V2_EXTERNAL", status="ACTIVE", description="",
            owner_user_id="u-owner", visibility="PRIVATE", publication_status="PRIVATE",
            created_at=now, updated_at=now,
        )
    )
    s.add(
        orm.StrategyVersionModel(
            strategy_version_id="sv-own", strategy_id="strat-own", version_no="1",
            market="CRYPTO", status="ACTIVE", spec={}, research_ref=None, created_at=now,
        )
    )
    # u-sub 有效订阅内置策略
    s.add(
        orm.StrategySubscriptionModel(
            user_id="u-sub", strategy_id="strat-builtin",
            valid_until=now + timedelta(days=30), status="active",
            created_at=now, updated_at=now,
        )
    )
    s.commit()
    s.close()
    return Session


def _events(Session):
    from zhice_os_v2.infrastructure.db import models as orm

    s = Session()
    try:
        return s.query(orm.NotificationEventModel).all()
    finally:
        s.close()


def _deliveries(Session):
    from zhice_os_v2.infrastructure.db import models as orm

    s = Session()
    try:
        return s.query(orm.NotificationDeliveryModel).all()
    finally:
        s.close()


def test_builtin_fill_fans_out_signal_to_subscriber_clean_payload(signal_env):
    from zhice_os_v2.application.services.notification_routing import (
        route_subscription_signal,
    )

    session = signal_env()
    n = route_subscription_signal(
        session,
        fill_id="fill-abc",
        sv_id="sv-1",
        side="BUY",
        ts_code="BTC/USDT",
        price=Decimal("50000"),
        market="CRYPTO",
        reason="金叉",
        fill_time=datetime.now(UTC),
    )
    session.commit()
    session.close()

    assert n == 1  # 一个活跃订阅者
    events = _events(signal_env)
    ev = [e for e in events if e.event_id == "signal-fill-abc-u-sub"]
    assert ev, f"未找到订阅者信号事件，events={[e.event_id for e in events]}"
    payload = ev[0].payload
    assert payload["signal"] == "entry"  # BUY=入场
    assert payload["ts_code"] == "BTC/USDT"
    # 合规红线：信号 payload 绝不含持仓量/成交额/盈亏
    for forbidden in ("qty", "amount", "realized_pnl", "cost", "account_id"):
        assert forbidden not in payload, f"信号 payload 泄露了 {forbidden}"
    # directed → delivery PENDING 行入队给订阅者
    dlvs = [d for d in _deliveries(signal_env) if d.user_id == "u-sub"]
    assert dlvs and dlvs[0].status == "PENDING"


def test_exit_signal_side_sell(signal_env):
    from zhice_os_v2.application.services.notification_routing import (
        route_subscription_signal,
    )

    session = signal_env()
    route_subscription_signal(
        session, fill_id="fill-exit", sv_id="sv-1", side="SELL",
        ts_code="BTC/USDT", price=Decimal("51000"), market="CRYPTO",
        exit_kind="TAKE_PROFIT", fill_time=datetime.now(UTC),
    )
    session.commit()
    session.close()
    ev = [e for e in _events(signal_env) if e.event_id == "signal-fill-exit-u-sub"]
    assert ev and ev[0].payload["signal"] == "exit"
    assert ev[0].payload["exit_kind"] == "TAKE_PROFIT"


def test_user_owned_strategy_not_fanned_out(signal_env):
    """用户自建策略（owner 非空）的成交不走订阅扇出（其信号由 route_fill 定向 owner）。"""
    from zhice_os_v2.application.services.notification_routing import (
        route_subscription_signal,
    )

    session = signal_env()
    n = route_subscription_signal(
        session, fill_id="fill-own", sv_id="sv-own", side="BUY",
        ts_code="ETH/USDT", price=Decimal("3000"), market="CRYPTO",
        fill_time=datetime.now(UTC),
    )
    session.commit()
    session.close()
    assert n == 0
    assert not [e for e in _events(signal_env) if e.event_id.startswith("signal-fill-own")]


def test_non_subscriber_gets_nothing(signal_env):
    """无有效订阅的用户不入队（list_active_subscriber_ids 已过滤）。"""
    from zhice_os_v2.application.services.notification_routing import (
        route_subscription_signal,
    )
    from zhice_os_v2.infrastructure.db import models as orm

    # 制造一个过期订阅者 u-old
    s = signal_env()
    now = datetime.now(UTC)
    s.add(
        orm.StrategySubscriptionModel(
            user_id="u-old", strategy_id="strat-builtin",
            valid_until=now - timedelta(days=1), status="active",
            created_at=now, updated_at=now,
        )
    )
    s.commit()
    s.close()

    session = signal_env()
    route_subscription_signal(
        session, fill_id="fill-x2", sv_id="sv-1", side="BUY",
        ts_code="BTC/USDT", price=Decimal("50000"), market="CRYPTO",
        fill_time=datetime.now(UTC),
    )
    session.commit()
    session.close()
    # 只有 u-sub（有效）拿到，u-old（过期）没有
    ev_users = {e.user_id for e in _events(signal_env) if e.event_id.startswith("signal-fill-x2")}
    assert ev_users == {"u-sub"}
