"""Phase C Task 4：用户级与实例级通知偏好。

语义覆盖（与计划 Task 4 一致）：
1. 市场报告收件人 = 有该市场启用实例的用户 ∩ 该市场 market_reports_enabled=True；
   无启用实例的用户即便偏好开也不在列；去重、稳定序（启用实例创建序）。
2. 实例级 should_notify fail-closed：未知实例/未知事件类型 → False；
   update_notifications 关 fill 后 should_notify("fill") 为 False。
3. strict 偏好模型：未知市场键 / 非 bool 开关值 / 非法 HH:MM / start==end / 缺键 /
   多余键 / 非法时区 / 非法 locale / 未知字段（extra=forbid）一律拒绝；
   跨午夜静音段（22:00-07:00）合法。
4. get_or_create 幂等；缺省：system_alerts=True、timezone=Asia/Shanghai、
   market_reports_enabled={}（等价全市场关，opt-in fail-closed）、locale 取用户
   locale（无用户行回退 zh）。
5. /my/notification-preferences owner-scoped：GET/PATCH 恒 JWT 本人、请求体绝不
   接受 user_id；bob 读/改只能落到 bob 自己的行（按 /my 范式，alice 偏好不可见
   且不受 bob 写影响）。
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
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


# ══════════════════════════════════════════════════════════════════════════════
# 隔离环境：preference + strategy-instance + account 三仓储共用同一 sqlite，
# 策略目录走内存 PersistencePort（与 conftest._instance_env 同范式，本文件自包含）。
# ══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def preference_env(monkeypatch: pytest.MonkeyPatch):
    import zhice_os_v2.infrastructure.repositories.notification_preference_repository as pref_repo_mod
    import zhice_os_v2.infrastructure.repositories.user_account_repository as acc_repo_mod
    import zhice_os_v2.infrastructure.repositories.user_strategy_repository as inst_repo_mod
    from zhice_os_v2.application.services.notification_preference_service import (
        NotificationPreferenceService,
    )
    from zhice_os_v2.application.services.user_account_service import UserAccountService
    from zhice_os_v2.application.services.user_strategy_service import (
        UserStrategyService,
    )
    from zhice_os_v2.infrastructure.db.base import Base
    from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(pref_repo_mod, "get_session", lambda: Session())
    monkeypatch.setattr(acc_repo_mod, "get_session", lambda: Session())
    monkeypatch.setattr(inst_repo_mod, "get_session", lambda: Session())

    db = InMemoryDatabase()
    account_service = UserAccountService()
    return SimpleNamespace(
        Session=Session,
        db=db,
        account_service=account_service,
        instance_service=UserStrategyService(
            db=db, account_service=account_service
        ),
        preference_service=NotificationPreferenceService(),
    )


@pytest.fixture
def pref_api(monkeypatch: pytest.MonkeyPatch):
    """挂好 /my/notification-preferences 的 TestClient + 隔离偏好库。"""
    import zhice_os_v2.infrastructure.repositories.notification_preference_repository as pref_repo_mod
    from zhice_os_v2.api.main import app
    from zhice_os_v2.infrastructure.db.base import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(pref_repo_mod, "get_session", lambda: Session())
    return SimpleNamespace(client=TestClient(app), Session=Session)


def _make_public_version(db, name: str, market: str):
    from zhice_os_v2.application.services.strategy_catalog_service import (
        StrategyCatalogService,
    )

    catalog = StrategyCatalogService(db)
    strat = catalog.create_public(name, market=market)
    return next(
        v for v in db.list_strategy_versions() if v.strategy_id == strat.strategy_id
    )


def _enable_market_instance(env, user: User, market: str, *, name: str | None = None):
    """注册该市场公共版本 → 建专属账户实例 → 启用。"""
    version = _make_public_version(env.db, name or f"pub-{market}", market)
    inst = env.instance_service.create(
        user, version.strategy_version_id, initial_capital=10_000
    )
    return env.instance_service.enable(user, inst.user_strategy_id)


def _seed_user(env, user_id: str, *, locale: str = "zh") -> None:
    from zhice_os_v2.infrastructure.db import models as orm

    s = env.Session()
    try:
        s.add(
            orm.UserModel(
                user_id=user_id,
                username=user_id,
                password_hash="x",
                role="USER",
                is_active=True,
                locale=locale,
                created_at=datetime.now(UTC),
            )
        )
        s.commit()
    finally:
        s.close()


@contextmanager
def _as_user(user: User) -> Iterator[None]:
    """以指定用户身份打 /my 端点（owner 恒取 JWT current_user）。"""
    from zhice_os_v2.api.dependencies import get_current_user
    from zhice_os_v2.api.main import app

    app.dependency_overrides[get_current_user] = lambda: user
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ══════════════════════════════════════════════════════════════════════════════
# 计划红测试 1：市场报告收件人（启用实例 ∩ 市场偏好）
# ══════════════════════════════════════════════════════════════════════════════


def test_market_report_recipients_require_enabled_instance_and_preference(
    preference_env, users
) -> None:
    """alice 有 US 启用实例 + US 报告开 → 在列；bob 无启用实例（偏好开）→ 不在列。"""
    _enable_market_instance(preference_env, users.alice, "US")
    svc = preference_env.preference_service
    svc.set_market_report(users.alice, "US", True)
    svc.set_market_report(users.bob, "US", True)
    assert svc.report_recipients("US") == [users.alice.user_id]


def test_report_recipients_empty_without_preference(preference_env, users) -> None:
    """启用实例但偏好未开（缺省 {} = 全市场关）→ 不在收件人列（opt-in fail-closed）。"""
    _enable_market_instance(preference_env, users.alice, "US")
    assert preference_env.preference_service.report_recipients("US") == []


def test_report_recipients_preference_off_excludes(preference_env, users) -> None:
    """偏好显式关 → 不在列。"""
    _enable_market_instance(preference_env, users.alice, "US")
    svc = preference_env.preference_service
    svc.set_market_report(users.alice, "US", True)
    svc.set_market_report(users.alice, "US", False)
    assert svc.report_recipients("US") == []


def test_report_recipients_market_isolated(preference_env, users) -> None:
    """市场互不串：alice 只开 US、bob 只开 HK。"""
    _enable_market_instance(preference_env, users.alice, "US")
    _enable_market_instance(preference_env, users.bob, "HK")
    svc = preference_env.preference_service
    svc.set_market_report(users.alice, "US", True)
    svc.set_market_report(users.bob, "HK", True)
    assert svc.report_recipients("US") == [users.alice.user_id]
    assert svc.report_recipients("HK") == [users.bob.user_id]


def test_report_recipients_dedup_stable_order(preference_env, users) -> None:
    """同一用户多实例去重；序 = 启用实例创建序（alice 先建）。"""
    _enable_market_instance(preference_env, users.alice, "US", name="pub-a")
    _enable_market_instance(preference_env, users.bob, "US", name="pub-b")
    _enable_market_instance(preference_env, users.alice, "US", name="pub-c")
    svc = preference_env.preference_service
    svc.set_market_report(users.alice, "US", True)
    svc.set_market_report(users.bob, "US", True)
    assert svc.report_recipients("US") == [users.alice.user_id, users.bob.user_id]


def test_report_recipients_disabled_instance_excluded(preference_env, users) -> None:
    """禁用实例后不再收该市场报告。"""
    inst = _enable_market_instance(preference_env, users.alice, "US")
    svc = preference_env.preference_service
    svc.set_market_report(users.alice, "US", True)
    preference_env.instance_service.disable(users.alice, inst.user_strategy_id)
    assert svc.report_recipients("US") == []


# ══════════════════════════════════════════════════════════════════════════════
# 计划红测试 2：实例级通知偏好（should_notify fail-closed）
# ══════════════════════════════════════════════════════════════════════════════


def test_instance_fill_disabled(preference_env, users) -> None:
    """update_notifications 关 fill → should_notify("fill") False。"""
    inst = _enable_market_instance(preference_env, users.alice, "US")
    svc = preference_env.instance_service
    assert svc.should_notify(inst.user_strategy_id, "fill") is True
    updated = svc.update_notifications(
        users.alice, inst.user_strategy_id, {"fill": False}
    )
    assert updated.notification_preferences["fill"] is False
    assert svc.should_notify(inst.user_strategy_id, "fill") is False


def test_should_notify_unknown_instance_fail_closed(preference_env) -> None:
    assert preference_env.instance_service.should_notify("no-such", "fill") is False


def test_should_notify_unknown_event_type_fail_closed(preference_env, users) -> None:
    inst = _enable_market_instance(preference_env, users.alice, "US")
    svc = preference_env.instance_service
    assert svc.should_notify(inst.user_strategy_id, "definitely_not_event") is False
    assert svc.should_notify("no-such", "definitely_not_event") is False


def test_update_notifications_rejects_unknown_key(preference_env, users) -> None:
    inst = _enable_market_instance(preference_env, users.alice, "US")
    with pytest.raises(ValueError, match="user_strategy.unknown_notification_key"):
        preference_env.instance_service.update_notifications(
            users.alice, inst.user_strategy_id, {"spam": True}
        )


def test_update_notifications_owner_scoped(preference_env, users) -> None:
    """bob 改不了 alice 实例的偏好（not-found 等价，不泄露存在性）。"""
    inst = _enable_market_instance(preference_env, users.alice, "US")
    with pytest.raises(LookupError):
        preference_env.instance_service.update_notifications(
            users.bob, inst.user_strategy_id, {"fill": False}
        )
    assert (
        preference_env.instance_service.should_notify(inst.user_strategy_id, "fill")
        is True
    )


# ══════════════════════════════════════════════════════════════════════════════
# get_or_create / update（服务层）
# ══════════════════════════════════════════════════════════════════════════════


def test_get_or_create_idempotent(preference_env, users) -> None:
    from zhice_os_v2.infrastructure.db import models as orm

    svc = preference_env.preference_service
    first = svc.get_or_create(users.alice.user_id)
    second = svc.get_or_create(users.alice.user_id)
    assert first.preference_id == second.preference_id
    s = preference_env.Session()
    try:
        assert (
            s.query(orm.UserNotificationPreferenceModel)
            .filter_by(user_id=users.alice.user_id)
            .count()
            == 1
        )
    finally:
        s.close()


def test_get_or_create_defaults(preference_env, users) -> None:
    pref = preference_env.preference_service.get_or_create(users.alice.user_id)
    assert pref.user_id == users.alice.user_id
    assert pref.system_alerts_enabled is True
    assert pref.market_reports_enabled == {}
    assert pref.quiet_hours is None
    assert pref.timezone == "Asia/Shanghai"
    assert pref.locale == "zh"


def test_get_or_create_locale_from_user(preference_env, users) -> None:
    """缺省 locale 取用户 locale（无用户行回退 zh，见上一测试）。"""
    _seed_user(preference_env, users.alice.user_id, locale="en")
    pref = preference_env.preference_service.get_or_create(users.alice.user_id)
    assert pref.locale == "en"


def test_update_partial_patch_semantics(preference_env, users) -> None:
    """None=不改（与 /my PATCH 范式一致）；market_reports_enabled 按键合并。"""
    from zhice_os_v2.application.services.notification_preference_service import (
        NotificationPreferenceBody,
    )

    svc = preference_env.preference_service
    svc.set_market_report(users.alice, "US", True)
    pref = svc.update(
        users.alice.user_id,
        NotificationPreferenceBody(
            system_alerts_enabled=False,
            quiet_hours={"start": "22:00", "end": "07:00"},  # 跨午夜合法
            timezone="UTC",
            locale="en",
        ),
    )
    assert pref.market_reports_enabled == {"US": True}  # 未提供 → 不改
    assert pref.system_alerts_enabled is False
    assert pref.quiet_hours == {"start": "22:00", "end": "07:00"}
    assert pref.timezone == "UTC"
    assert pref.locale == "en"


def test_update_market_reports_merge(preference_env, users) -> None:
    """市场开关合并语义：只改提供的市场，保留其余。"""
    from zhice_os_v2.application.services.notification_preference_service import (
        NotificationPreferenceBody,
    )

    svc = preference_env.preference_service
    svc.set_market_report(users.alice, "US", True)
    svc.set_market_report(users.alice, "CN", True)
    pref = svc.update(
        users.alice.user_id,
        NotificationPreferenceBody(market_reports_enabled={"HK": False}),
    )
    assert pref.market_reports_enabled == {"US": True, "CN": True, "HK": False}


def test_set_market_report_rejects_unknown_market(preference_env, users) -> None:
    with pytest.raises(ValueError, match="notification_preference.unknown_market"):
        preference_env.preference_service.set_market_report(users.alice, "JP", True)


def test_update_empty_body_noop(preference_env, users) -> None:
    from zhice_os_v2.application.services.notification_preference_service import (
        NotificationPreferenceBody,
    )

    svc = preference_env.preference_service
    before = svc.set_market_report(users.alice, "US", True)
    after = svc.update(users.alice.user_id, NotificationPreferenceBody())
    assert after.preference_id == before.preference_id
    assert after.market_reports_enabled == {"US": True}


# ══════════════════════════════════════════════════════════════════════════════
# strict Pydantic 模型
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "body",
    [
        {"market_reports_enabled": {"JP": True}},  # 未知市场
        {"market_reports_enabled": {"US": "yes"}},  # 开关值非 bool
        {"system_alerts_enabled": "off"},  # 开关值非 bool
        {"quiet_hours": {"start": "25:00", "end": "07:00"}},  # 非法 HH:MM（时）
        {"quiet_hours": {"start": "22:60", "end": "07:00"}},  # 非法 HH:MM（分）
        {"quiet_hours": {"start": "22:00", "end": "7:00"}},  # 非补零 HH:MM
        {"quiet_hours": {"start": "2200", "end": "0700"}},  # 缺冒号
        {"quiet_hours": {"start": "22:00", "end": "22:00"}},  # start == end
        {"quiet_hours": {"start": "22:00"}},  # 缺 end
        {"quiet_hours": {"end": "07:00"}},  # 缺 start
        {"quiet_hours": {"start": "22:00", "end": "07:00", "days": ["mon"]}},  # 多余键
        {"timezone": "Mars/Olympus"},  # 非法时区
        {"timezone": ""},  # 空时区
        {"locale": "fr"},  # 非法 locale
        {"unexpected_field": 1},  # 未知字段（extra=forbid）
    ],
)
def test_preference_body_strict_rejects(body: dict) -> None:
    from zhice_os_v2.application.services.notification_preference_service import (
        NotificationPreferenceBody,
    )

    with pytest.raises(ValidationError):
        NotificationPreferenceBody.model_validate(body)


def test_preference_body_cross_midnight_valid() -> None:
    """跨午夜静音段（22:00-07:00）合法。"""
    from zhice_os_v2.application.services.notification_preference_service import (
        NotificationPreferenceBody,
    )

    body = NotificationPreferenceBody(
        quiet_hours={"start": "22:00", "end": "07:00"}
    )
    assert body.quiet_hours is not None
    assert body.quiet_hours.start == "22:00"
    assert body.quiet_hours.end == "07:00"


def test_preference_body_same_day_valid() -> None:
    from zhice_os_v2.application.services.notification_preference_service import (
        NotificationPreferenceBody,
    )

    body = NotificationPreferenceBody(
        quiet_hours={"start": "01:00", "end": "06:30"}
    )
    assert body.quiet_hours is not None


def test_preference_body_empty_ok() -> None:
    from zhice_os_v2.application.services.notification_preference_service import (
        NotificationPreferenceBody,
    )

    body = NotificationPreferenceBody.model_validate({})
    assert body.market_reports_enabled is None
    assert body.system_alerts_enabled is None
    assert body.quiet_hours is None
    assert body.timezone is None
    assert body.locale is None


def test_preference_body_all_markets_ok() -> None:
    from zhice_os_v2.application.services.notification_preference_service import (
        NotificationPreferenceBody,
    )

    body = NotificationPreferenceBody(
        market_reports_enabled={"CN": True, "US": True, "HK": True, "CRYPTO": False}
    )
    assert body.market_reports_enabled == {
        "CN": True,
        "US": True,
        "HK": True,
        "CRYPTO": False,
    }


# ══════════════════════════════════════════════════════════════════════════════
# API /my/notification-preferences（owner-scoped）
# ══════════════════════════════════════════════════════════════════════════════


def test_api_get_returns_defaults(pref_api, users) -> None:
    with _as_user(users.alice):
        resp = pref_api.client.get(_URL)
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == users.alice.user_id
    assert body["channel"] == "telegram"
    assert body["system_alerts_enabled"] is True
    assert body["market_reports_enabled"] == {}
    assert body["quiet_hours"] is None
    assert body["timezone"] == "Asia/Shanghai"
    assert body["locale"] == "zh"


def test_api_patch_roundtrip_and_persisted(pref_api, users) -> None:
    payload = {
        "market_reports_enabled": {"US": True},
        "system_alerts_enabled": False,
        "quiet_hours": {"start": "22:00", "end": "07:00"},
        "timezone": "UTC",
        "locale": "en",
    }
    with _as_user(users.alice):
        resp = pref_api.client.patch(_URL, json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body["market_reports_enabled"] == {"US": True}
        assert body["system_alerts_enabled"] is False
        assert body["quiet_hours"] == {"start": "22:00", "end": "07:00"}
        assert body["timezone"] == "UTC"
        assert body["locale"] == "en"
        # GET 读回已持久化
        got = pref_api.client.get(_URL)
        assert got.status_code == 200
        assert got.json()["timezone"] == "UTC"
        assert got.json()["market_reports_enabled"] == {"US": True}


def test_api_patch_partial_keeps_others(pref_api, users) -> None:
    with _as_user(users.alice):
        first = pref_api.client.patch(
            _URL, json={"market_reports_enabled": {"US": True}}
        )
        assert first.status_code == 200
        second = pref_api.client.patch(_URL, json={"system_alerts_enabled": False})
        assert second.status_code == 200
        assert second.json()["market_reports_enabled"] == {"US": True}  # 未提供不改
        assert second.json()["system_alerts_enabled"] is False


def test_api_patch_empty_body_noop(pref_api, users) -> None:
    with _as_user(users.alice):
        resp = pref_api.client.patch(_URL, json={})
    assert resp.status_code == 200
    assert resp.json()["system_alerts_enabled"] is True


def test_api_requires_auth(pref_api) -> None:
    assert pref_api.client.get(_URL).status_code == 401
    assert pref_api.client.patch(_URL, json={}).status_code == 401


def test_api_patch_viewer_own_preferences_allowed(pref_api) -> None:
    """VIEWER 可维护本人通知偏好，owner 仍恒取 JWT 身份。"""
    viewer = User("u-viewer", "viewer", Role.VIEWER)
    with _as_user(viewer):
        resp = pref_api.client.patch(_URL, json={"system_alerts_enabled": False})
    assert resp.status_code == 200
    assert resp.json()["user_id"] == viewer.user_id
    assert resp.json()["system_alerts_enabled"] is False
    # GET 仍可（仅要求登录）
    with _as_user(viewer):
        assert pref_api.client.get(_URL).status_code == 200


def test_api_bob_cannot_read_or_modify_alice(pref_api, users) -> None:
    """按 /my 范式（无资源 id）：bob 读到的是自己的缺省行、写只落自己行。"""
    with _as_user(users.alice):
        resp = pref_api.client.patch(_URL, json={"timezone": "UTC", "locale": "en"})
        assert resp.status_code == 200

    with _as_user(users.bob):
        got = pref_api.client.get(_URL)
        assert got.status_code == 200
        assert got.json()["timezone"] == "Asia/Shanghai"  # 非 alice 的 UTC
        assert got.json()["locale"] == "zh"
        patched = pref_api.client.patch(_URL, json={"system_alerts_enabled": False})
        assert patched.status_code == 200
        assert patched.json()["user_id"] == users.bob.user_id

    with _as_user(users.alice):
        again = pref_api.client.get(_URL)
        assert again.json()["timezone"] == "UTC"  # bob 的写不影响 alice 行
        assert again.json()["system_alerts_enabled"] is True


@pytest.mark.parametrize(
    "bad",
    [
        {"market_reports_enabled": {"JP": True}},
        {"market_reports_enabled": {"US": "yes"}},
        {"quiet_hours": {"start": "25:00", "end": "07:00"}},
        {"quiet_hours": {"start": "22:00", "end": "22:00"}},
        {"quiet_hours": {"start": "22:00"}},
        {"quiet_hours": {"start": "22:00", "end": "07:00", "days": ["mon"]}},
        {"timezone": "Mars/Olympus"},
        {"locale": "fr"},
        {"unexpected_field": 1},
    ],
)
def test_api_patch_strict_422(pref_api, users, bad: dict) -> None:
    with _as_user(users.alice):
        resp = pref_api.client.patch(_URL, json=bad)
    assert resp.status_code == 422


# ══════════════════════════════════════════════════════════════════════════════
# Phase C Task 6：定向模板（zh/en × 11 类事件）+ transport 纯化
# ══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def template_service():
    from zhice_os_v2.application.services.notification_template_service import (
        NotificationTemplateService,
    )

    return NotificationTemplateService()


_ALL_EVENT_TYPES = [
    "signal",
    "plan",
    "approval",
    "risk",
    "order",
    "fill",
    "stop_loss",
    "take_profit",
    "runtime_error",
    "market_report",
    "user_report",
]

_FULL_PAYLOAD = {
    "side": "BUY",
    "symbol": "600519.SH",
    "market": "CN",
    "price": 1680,
    "qty": 100,
    "amount": 168000,
    "realized_pnl": 120.5,
    "strategy": "趋势动量v1",
    "reason": "多头排列",
    "sell_reason": "止损触发",
    "status": "approved",
    "kind": "max_drawdown",
    "detail": "回撤超限",
    "source": "scheduler",
    "error": "boom",
    "title": "晨报",
    "narrative": "市场走高",
    "time": "2026-09-06T10:30:00",
    "fill_time": "2026-09-06T10:30:00",
    "ts_code": "600519.SH",
    "strategy_name": "趋势动量v1",
}


def _english_fill_payload() -> dict:
    return {
        "side": "BUY",
        "symbol": "AAPL",
        "market": "US",
        "price": 175,
        "qty": 10,
        "amount": 1750,
        "strategy": "TrendMomentum",
        "reason": "golden cross",
    }


# ── 计划红测试：escaping / locale ────────────────────────────────────────────


def test_dynamic_html_is_escaped(template_service) -> None:
    """策略名含 <>& 时必须转义（parse_mode=HTML 下防破格式/注入）。"""
    text = template_service.render("fill", locale="zh", payload={"strategy": "A&B <突破>"})
    assert "A&amp;B &lt;突破&gt;" in text
    # 原始未转义串绝不能出现
    assert "A&B" not in text
    assert "<突破>" not in text


def test_english_fill_template(template_service) -> None:
    assert "Bought" in template_service.render(
        "fill", locale="en", payload=_english_fill_payload()
    )


def test_all_dynamic_values_escaped_in_english_fill(template_service) -> None:
    text = template_service.render(
        "fill",
        locale="en",
        payload={**_english_fill_payload(),
                 "strategy": "S&P <500>", "reason": "q&e"},
    )
    assert "S&amp;P &lt;500&gt;" in text
    assert "q&amp;e" in text
    assert "S&P" not in text


# ── 11 类 × zh/en 冒烟 ───────────────────────────────────────────────────────


@pytest.mark.parametrize("locale", ["zh", "en"])
@pytest.mark.parametrize("event_type", _ALL_EVENT_TYPES)
def test_all_event_types_render_non_empty(template_service, event_type: str, locale: str) -> None:
    """每类事件 × 双 locale：满 payload 渲染无异常且非空。"""
    text = template_service.render(event_type, locale=locale, payload=dict(_FULL_PAYLOAD))
    assert text.strip()


def test_render_accepts_enum_instance(template_service) -> None:
    from zhice_os_v2.application.services.notification_outbox_service import NotificationType

    text = template_service.render(
        NotificationType.FILL, locale="zh", payload={"symbol": "600519.SH"}
    )
    assert "600519.SH" in text


def test_unknown_event_type_raises(template_service) -> None:
    with pytest.raises(ValueError):
        template_service.render("definitely_not_event", locale="zh", payload={})


# ── 模板健壮性：缺键省行 / 数字格式 / 风格对齐 ──────────────────────────────


def test_fill_missing_payload_keys_lines_omitted(template_service) -> None:
    """缺键 → 该行省略，不 KeyError、不出占位行（只剩头行）。"""
    text = template_service.render("fill", locale="zh", payload={})
    assert text.strip()
    assert len(text.splitlines()) == 1
    for absent in ("策略", "理由", "盈亏", "价 "):
        assert absent not in text


def test_zh_fill_matches_notify_fill_style(template_service) -> None:
    """zh fill 首行/价量额行/策略行/时间 与现有 notify_fill 观感一致。"""
    text = template_service.render(
        "fill",
        locale="zh",
        payload={
            "side": "BUY",
            "symbol": "600519.SH",
            "market": "CN",
            "price": "1680",
            "qty": "100",
            "amount": "168000",
            "strategy": "趋势动量v1",
            "reason": "趋势动量:多头排列",
            "time": "2026-09-06T10:30:00",
        },
    )
    lines = text.splitlines()
    assert lines[0] == "🟢 买入 600519.SH [CN]"
    assert "价 1680 · 量 100 · 额 168000" in lines
    assert "策略 趋势动量v1" in lines
    assert "理由 趋势动量:多头排列" in lines
    assert lines[-1] == "2026-09-06 10:30:00"


def test_fill_number_and_pnl_formatting(template_service) -> None:
    """数字复用 _fmt_num 语义（:g 去尾零）；盈亏 +.2f 带符号。"""
    text = template_service.render(
        "fill",
        locale="zh",
        payload={
            "side": "SELL",
            "symbol": "AAPL",
            "market": "US",
            "price": "175",
            "qty": "10",
            "amount": "1750",
            "realized_pnl": "120.5",
        },
    )
    assert "🔴 卖出 AAPL [US]" in text.splitlines()
    assert "价 175 · 量 10 · 额 1750" in text
    assert "盈亏 +120.50" in text


def test_fill_accepts_legacy_key_aliases(template_service) -> None:
    """ts_code/strategy_name/sell_reason/fill_time 旧键名兼容。"""
    text = template_service.render(
        "fill",
        locale="zh",
        payload={
            "side": "SELL",
            "ts_code": "AAPL",
            "market": "US",
            "strategy_name": "趋势动量v1",
            "sell_reason": "止损触发",
            "fill_time": "2026-09-06T10:30:00",
        },
    )
    assert "🔴 卖出 AAPL [US]" in text.splitlines()
    assert "策略 趋势动量v1" in text
    assert "理由 止损触发" in text
    assert "2026-09-06 10:30:00" in text


def test_market_report_head_bold_and_narrative_escaped(template_service) -> None:
    """报告头对齐现有广播格式（📊 <b>市场 标题</b>）；narrative 动态转义。"""
    text = template_service.render(
        "market_report",
        locale="zh",
        payload={"market": "CN", "title": "晨报", "narrative": "指数<涨>&回落"},
    )
    assert text.startswith("📊 <b>CN 晨报</b>")
    assert "指数&lt;涨&gt;&amp;回落" in text
    assert "指数<涨>" not in text


def test_user_report_english(template_service) -> None:
    text = template_service.render(
        "user_report",
        locale="en",
        payload={"title": "Daily Report", "narrative": "PnL up 3%"},
    )
    assert "Daily Report" in text
    assert "PnL up 3%" in text


# ── transport 纯化：TelegramSendResult ──────────────────────────────────────


def _transport_env(monkeypatch: pytest.MonkeyPatch):
    """token 有、proxy 无（消除 .env.local 副作用），返回 httpx 模块供打桩。"""
    from zhice_os_v2.application.services import telegram_notifier as tn

    monkeypatch.setattr(tn, "_bot_token", lambda: "TOKEN")
    monkeypatch.setattr(tn, "_proxy", lambda: None)
    import httpx

    return tn, httpx


class _FakeResp:
    def __init__(self, status_code: int, body: dict | None = None) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> dict:
        if self._body is None:
            raise ValueError("no body")
        return self._body


def test_send_telegram_200_ok(monkeypatch) -> None:
    tn, httpx = _transport_env(monkeypatch)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResp(200))
    res = tn.send_telegram("123456789", "hi")
    assert res.ok is True
    assert res.status_code == 200
    assert res.error_code is None
    assert res.retry_after is None


def test_send_telegram_429_parses_retry_after(monkeypatch) -> None:
    tn, httpx = _transport_env(monkeypatch)
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _FakeResp(429, {"parameters": {"retry_after": 30}})
    )
    res = tn.send_telegram("123456789", "hi")
    assert res.ok is False
    assert res.status_code == 429
    assert res.error_code == "http_429"
    assert res.retry_after == 30


def test_send_telegram_429_without_body(monkeypatch) -> None:
    """429 但 body 不可解析 → retry_after=None（ok 仍 False、error_code 稳定）。"""
    tn, httpx = _transport_env(monkeypatch)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResp(429))
    res = tn.send_telegram("123456789", "hi")
    assert res.ok is False
    assert res.error_code == "http_429"
    assert res.retry_after is None


def test_send_telegram_http_error_code(monkeypatch) -> None:
    tn, httpx = _transport_env(monkeypatch)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResp(502))
    res = tn.send_telegram("123456789", "hi")
    assert res.ok is False
    assert res.status_code == 502
    assert res.error_code == "http_502"


def test_send_telegram_network_error(monkeypatch) -> None:
    tn, httpx = _transport_env(monkeypatch)

    def _boom(*a, **k):
        raise httpx.ConnectError("connection refused https://api.telegram.org/botSECRET/sendMessage")

    monkeypatch.setattr(httpx, "post", _boom)
    res = tn.send_telegram("123456789", "hi")
    assert res.ok is False
    assert res.status_code is None
    assert res.error_code == "network"


def test_send_telegram_no_token_stable_error_code(monkeypatch) -> None:
    from zhice_os_v2.application.services import telegram_notifier as tn

    monkeypatch.setattr(tn, "_bot_token", lambda: "")
    res = tn.send_telegram("123456789", "hi")
    assert res.ok is False
    assert res.error_code == "not_configured"


def test_send_telegram_log_redacts_chat_id(monkeypatch, caplog) -> None:
    """日志绝不落完整 chat_id（只允许尾 4 位脱敏形态）。"""
    import logging

    tn, httpx = _transport_env(monkeypatch)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResp(403))
    chat_id = "123456789012"
    with caplog.at_level(logging.WARNING, logger=tn.__name__):
        tn.send_telegram(chat_id, "hi")
    assert chat_id not in caplog.text
    assert "9012" in caplog.text  # 尾 4 位脱敏形态仍可观测


def test_send_telegram_is_pure_transport() -> None:
    """结构断言：send_telegram 不查收件人、无广播逻辑（transport 降级）。"""
    import inspect

    from zhice_os_v2.application.services import telegram_notifier as tn

    src = inspect.getsource(tn.send_telegram)
    assert "UserRepository" not in src
    assert "list_telegram_chat_ids" not in src
    assert "broadcast" not in src


def test_broadcast_uses_send_ok(monkeypatch) -> None:
    """broadcast 回归：send ok 计数、fail 不计（行为语义与旧真值判断一致）。"""
    from zhice_os_v2.application.services import telegram_notifier as tn
    from zhice_os_v2.application.services.telegram_notifier import TelegramSendResult

    monkeypatch.setattr(tn, "_bot_token", lambda: "TOKEN")
    results = {
        "1": TelegramSendResult(ok=True, status_code=200),
        "2": TelegramSendResult(ok=False, status_code=429, error_code="http_429", retry_after=30),
        "3": TelegramSendResult(ok=True, status_code=200),
    }
    monkeypatch.setattr(
        tn, "send_telegram", lambda cid, text: results[cid]
    )
    from unittest.mock import MagicMock

    import zhice_os_v2.config.settings as st_mod

    fake_settings = MagicMock()
    fake_settings.persistence_backend = "sql"
    monkeypatch.setattr(st_mod, "get_settings", lambda: fake_settings)
    import zhice_os_v2.infrastructure.repositories.user_repository as ur

    fake_repo = MagicMock()
    fake_repo.list_active_notification_recipients.return_value = list(results)
    monkeypatch.setattr(ur, "UserRepository", lambda: fake_repo)
    assert tn.broadcast_telegram("hi") == 2
    fake_repo.list_active_notification_recipients.assert_called_once_with("telegram")
