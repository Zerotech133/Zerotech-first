"""VIEWER 只读 RBAC 单测：禁写中间件 + deny_viewer 依赖。

中间件用 token payload role(不查库)，故 create_token 造 token 即可测。
deny_viewer 是纯函数，直接传 User 断言。
端点级 deny_viewer/detail 脱敏的库依赖路径由端到端(curl)覆盖。
"""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from zhice_os_v2.api.dependencies import deny_viewer
from zhice_os_v2.api.main import app
from zhice_os_v2.application.services.token_service import create_token
from zhice_os_v2.config.settings import AppSettings
from zhice_os_v2.domain.security.user import Role, User

client = TestClient(app)

_VIEWER_403 = {"code": "auth.viewer_readonly"}


def _h(user_id, username, role):
    return {"Authorization": f"Bearer {create_token(user_id, username, role)}"}


# ── deny_viewer 纯函数 ──────────────────────────────────────────────
def test_deny_viewer_blocks_viewer():
    with pytest.raises(HTTPException) as e:
        deny_viewer(User(user_id="x", username="v", role=Role.VIEWER))
    assert e.value.status_code == 403


def test_deny_viewer_allows_admin():
    u = User(user_id="x", username="a", role=Role.ADMIN)
    assert deny_viewer(u) is u


# ── 禁写中间件（token role，不查库）──────────────────────────────────
def test_viewer_write_blocked_by_middleware():
    r = client.post("/api/v1/trade/simulate", headers=_h("u-v", "viewer", "VIEWER"), json={})
    assert r.status_code == 403
    assert r.json().get("detail") == _VIEWER_403


def test_viewer_self_service_exempt():
    # PATCH /auth/me 在豁免清单：不应被 viewer_readonly 中间件拦（走下游，库里无此用户→401，但非 viewer_readonly）
    r = client.patch("/api/v1/auth/me", headers=_h("u-v", "viewer", "VIEWER"), json={"locale": "en"})
    assert r.json().get("detail") != _VIEWER_403


# ── WxPusher 本人绑定端点：VIEWER 自助白名单（真实端点断言）─────────────────


@pytest.fixture
def wxpusher_env(monkeypatch):
    """隔离 sqlite + mock 官方 HTTP + 启用 WxPusher 绑定，供真实端点断言。"""
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from typing import Any

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import zhice_os_v2.api.routers.integrations_wxpusher as wx_mod
    import zhice_os_v2.api.routers.my_notification_bindings as my_mod
    import zhice_os_v2.application.services.notification_binding_service as svc_mod
    from zhice_os_v2.api.dependencies import (
        get_current_user,
        get_notification_binding_service,
    )
    from zhice_os_v2.application.services.notification_binding_service import (
        NotificationBindingService,
    )
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.base import Base
    from zhice_os_v2.infrastructure.notifications.wxpusher_binding_client import (
        WxPusherBindingClient,
    )
    from zhice_os_v2.infrastructure.notifications.wxpusher_transport import WxPusherTransport

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    class _MockHttp:
        """记录式替身：二维码创建成功 / 扫码未扫 / 发送成功（绝不真外呼）。"""

        def __init__(self) -> None:
            self.calls: list[str] = []

        def post(self, url: str, *, json: dict, timeout: float):
            self.calls.append(url)
            if "send/message" in url:
                return SimpleNamespace(
                    status_code=200,
                    headers={},
                    json=lambda: {
                        "success": True,
                        "code": 1000,
                        "data": [{"uid": "UID_x", "code": 1000}],
                    },
                )
            return SimpleNamespace(
                status_code=200,
                headers={},
                json=lambda: {
                    "success": True,
                    "code": 1000,
                    "data": {"code": "QRC_1", "url": "https://img", "shortUrl": "https://s"},
                },
            )

        def get(self, url: str, *, params: Any, timeout: float):
            self.calls.append(url)
            return SimpleNamespace(
                status_code=200, headers={}, json=lambda: {"success": True, "code": 1000, "data": None}
            )

    http = _MockHttp()
    monkeypatch.delenv("WXPUSHER_APP_TOKEN", raising=False)
    monkeypatch.delenv("WXPUSHER_APP_ID", raising=False)
    monkeypatch.delenv("WXPUSHER_APP_KEY", raising=False)
    monkeypatch.delenv("WXPUSHER_WEBHOOK_PATH_KEY", raising=False)
    monkeypatch.delenv("WXPUSHER_BINDING_ENABLED", raising=False)
    monkeypatch.delenv("WXPUSHER_DELIVERY_ENABLED", raising=False)
    settings = AppSettings(
        _env_file=None,
        wxpusher_binding_enabled=True,
        wxpusher_app_token="AT_synthetic",
        wxpusher_app_id=123,
        wxpusher_app_key="AK_synthetic",
        wxpusher_webhook_path_key="pk_" + "s" * 40,
    )
    service = NotificationBindingService(
        binding_client=WxPusherBindingClient(app_token="AT_synthetic", timeout=3.0, client=http),
        transport=WxPusherTransport(app_token="AT_synthetic", timeout=3.0, client=http),
    )
    monkeypatch.setattr(svc_mod, "get_session", lambda: Session())
    monkeypatch.setattr(svc_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(my_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(wx_mod, "get_settings", lambda: settings)

    with Session() as session:
        session.add(
            orm.UserModel(
                user_id="u-v",
                username="viewer",
                password_hash="x",
                role="VIEWER",
                is_active=True,
                locale="zh",
                created_at=datetime.now(UTC),
            )
        )
        session.commit()

    app.dependency_overrides[get_notification_binding_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: User(
        user_id="u-v", username="viewer", role=Role.VIEWER
    )
    try:
        yield {"client": TestClient(app), "Session": Session}
    finally:
        app.dependency_overrides.pop(get_notification_binding_service, None)
        app.dependency_overrides.pop(get_current_user, None)


def test_viewer_can_manage_own_wxpusher_binding(wxpusher_env):
    """POST challenge / GET / POST test / DELETE 全部真实通过 VIEWER guard 与端点。"""
    c = wxpusher_env["client"]
    create = c.post("/api/v1/auth/me/notification-bindings/wxpusher/challenge")
    assert create.status_code == 200, create.text
    assert set(create.json()) == {"qr_image_url", "expires_at"}

    listing = c.get("/api/v1/auth/me/notification-bindings")
    assert listing.status_code == 200, listing.text
    assert listing.json() == []

    unbound_test = c.post("/api/v1/auth/me/notification-bindings/wxpusher/test")
    assert unbound_test.status_code == 409
    assert unbound_test.json()["detail"] == {"code": "notification.binding_required"}

    unbind = c.delete("/api/v1/auth/me/notification-bindings/wxpusher")
    assert unbind.status_code == 200
    assert unbind.json() == {"ok": True}


@pytest.mark.parametrize("method", ["patch", "put"])
def test_viewer_patch_put_on_binding_prefix_still_blocked(method):
    """负向：白名单只有 POST/DELETE 前缀，PATCH/PUT 仍被 VIEWER 只读中间件拒绝。"""
    r = getattr(client, method)(
        "/api/v1/auth/me/notification-bindings/wxpusher",
        headers=_h("u-v", "viewer", "VIEWER"),
        json={},
    )
    assert r.status_code == 403
    assert r.json().get("detail") == _VIEWER_403


def test_viewer_notification_preference_allowlist_is_exact():
    # /extra 不是本人偏好端点，必须继续被 VIEWER 只读中间件拦截。
    r = client.patch(
        "/api/v1/my/notification-preferences/extra",
        headers=_h("u-v", "viewer", "VIEWER"),
        json={},
    )
    assert r.status_code == 403
    assert r.json().get("detail") == _VIEWER_403


def test_viewer_unrelated_auth_me_prefix_write_still_blocked():
    r = client.post(
        "/api/v1/auth/me/not-allowed",
        headers=_h("u-v", "viewer", "VIEWER"),
        json={},
    )
    assert r.status_code == 403
    assert r.json().get("detail") == _VIEWER_403


def test_non_viewer_not_blocked_by_middleware():
    # ADMIN 写不被 viewer_readonly 中间件拦（下游可能因库无此用户 401，但绝非 viewer_readonly 403）
    r = client.post("/api/v1/trade/simulate", headers=_h("u-a", "admin", "ADMIN"), json={})
    assert r.json().get("detail") != _VIEWER_403


def test_viewer_get_not_blocked_by_write_middleware():
    # GET 不是写方法，中间件放行（读端点的 deny_viewer 另测）
    r = client.get("/api/v1/health")
    assert r.status_code != 403


# ── VIEWER 敏感读端点（deep-test 4a/4b：泄露总权益/他人账户/成交明细）──

def test_viewer_blocked_accounts_stats():
    """4a：/accounts/stats 泄露总权益+账户余额 → deny_viewer 403。"""
    r = client.get("/api/v1/accounts/stats", headers=_h("u-v", "viewer", "VIEWER"))
    assert r.status_code == 403
    assert r.json()["detail"] == {"code": "auth.viewer_forbidden"}


def test_viewer_blocked_accounts_stats_by_strategy():
    """4a：/accounts/stats/by-strategy 泄露他人私有策略账户 → deny_viewer 403。"""
    r = client.get("/api/v1/accounts/stats/by-strategy", headers=_h("u-v", "viewer", "VIEWER"))
    assert r.status_code == 403
    assert r.json()["detail"] == {"code": "auth.viewer_forbidden"}


def test_viewer_blocked_system_audit_recent():
    """4b：/system/audit/recent 泄露成交明细（标的/方向/数量）→ deny_viewer 403。"""
    r = client.get("/api/v1/system/audit/recent", headers=_h("u-v", "viewer", "VIEWER"))
    assert r.status_code == 403
    assert r.json()["detail"] == {"code": "auth.viewer_forbidden"}


# ── B4 补拦：VIEWER 三处泄露（B1 复核发现，同 deny_viewer 体系）─────────

def test_viewer_blocked_accounts_ext_risk_summary():
    """B4：/accounts-ext/risk-summary 泄露各账户仓位/今日盈亏 → deny_viewer 403。"""
    r = client.get("/api/v1/accounts-ext/risk-summary", headers=_h("u-v", "viewer", "VIEWER"))
    assert r.status_code == 403
    assert r.json()["detail"] == {"code": "auth.viewer_forbidden"}


def test_viewer_blocked_accounts_ext_attribution():
    """B4：/accounts-ext/attribution 泄露分策略已实现盈亏归因 → deny_viewer 403。"""
    r = client.get("/api/v1/accounts-ext/attribution", headers=_h("u-v", "viewer", "VIEWER"))
    assert r.status_code == 403
    assert r.json()["detail"] == {"code": "auth.viewer_forbidden"}


def test_viewer_blocked_alerts_list():
    """B4：GET /alerts 泄露运营告警 → deny_viewer 403。"""
    r = client.get("/api/v1/alerts", headers=_h("u-v", "viewer", "VIEWER"))
    assert r.status_code == 403
    assert r.json()["detail"] == {"code": "auth.viewer_forbidden"}


# ── USER owner-scope 限定（token role，不查库）──────────────────────
_USER_SCOPE_403 = {"code": "auth.user_owner_scope_only"}


def test_user_can_write_owner_scoped_path():
    # /api/v1/my/* 前缀不被 scope-only 中间件拦（本任务阶段该路由尚未创建，
    # 放行后自然 404，但 detail 绝非 scope-only 码）。
    response = client.post(
        "/api/v1/my/strategy-instances",
        headers=_h("u-user", "alice", "USER"),
        json={},
    )
    assert response.json().get("detail") != _USER_SCOPE_403


def test_user_cannot_write_global_path():
    response = client.post(
        "/api/v1/trade/simulate",
        headers=_h("u-user", "alice", "USER"),
        json={},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == _USER_SCOPE_403


# ── /my/strategies API 级 RBAC 冒烟 ─────────────────────────────────
def test_my_strategies_user_can_create():
    r = client.post(
        "/api/v1/my/strategies",
        headers=_h("u-user", "alice", "USER"),
        json={"strategy_name": "我的私有", "market": "CN"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["visibility"] == "PRIVATE"
    assert body["owner_user_id"] == "u-user"  # owner 恒取自 JWT


def test_my_strategies_ignores_body_owner_user_id():
    # 请求体即使塞 owner_user_id 也不生效（防越权归属）
    r = client.post(
        "/api/v1/my/strategies",
        headers=_h("u-user", "alice", "USER"),
        json={"strategy_name": "越权尝试", "market": "CN", "owner_user_id": "u-someone-else"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["owner_user_id"] == "u-user"


def test_my_strategies_viewer_blocked():
    r = client.post(
        "/api/v1/my/strategies",
        headers=_h("u-v", "viewer", "VIEWER"),
        json={"strategy_name": "x", "market": "CN"},
    )
    assert r.status_code == 403
    assert r.json().get("detail") == _VIEWER_403  # viewer_readonly 中间件先拦


def test_my_strategies_trader_blocked():
    r = client.post(
        "/api/v1/my/strategies",
        headers=_h("u-t", "trader", "TRADER"),
        json={"strategy_name": "x", "market": "CN"},
    )
    assert r.status_code == 403  # 端点级 require_roles 挡非 USER/ADMIN
