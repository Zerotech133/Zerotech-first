"""单元 B Task B1-7：管理员权益查询/详情/审计分页 API（只读，ADMIN）。

隔离内存 sqlite：建全表、跨模块 monkeypatch get_session 到同一库、
dependencies.persistence_backend='sql'；管理员身份用 token_service.create_token
直接签发（GET 不触发写限制中间件）。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.domain.security.password import hash_password
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.base import Base

NOW = datetime.now(UTC)
FUTURE = NOW + timedelta(days=365)
PAST = NOW - timedelta(days=365)


@pytest.fixture()
def api_env(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    s = Session()
    # 管理员（用于鉴权）
    s.add(orm.UserModel(
        user_id="admin", username="admin", password_hash=hash_password("123456"),
        role="ADMIN", is_active=True, auth_version=1,
        created_at=NOW - timedelta(days=10),
    ))
    # 目标用户（created_at 各异用于排序断言）
    s.add(orm.UserModel(
        user_id="u-member", username="active-member", password_hash="x", role="USER",
        is_active=True, auth_version=1, created_at=NOW - timedelta(days=3),
    ))
    s.add(orm.UserModel(
        user_id="u-free", username="free-user", password_hash="x", role="USER",
        is_active=True, auth_version=1, created_at=NOW - timedelta(days=2),
    ))
    s.add(orm.UserModel(
        user_id="u-revoked", username="revoked-user", password_hash="x", role="VIEWER",
        is_active=True, auth_version=1, created_at=NOW - timedelta(days=1),
    ))
    # 策略（订阅引用）
    for sid in ("strat-a", "strat-b", "strat-c"):
        s.add(orm.StrategyModel(
            strategy_id=sid, strategy_name=sid, market="US",
            status="ACTIVE", owner_user_id=None,
            visibility="PUBLIC", publication_status="PUBLISHED",
            created_at=NOW, updated_at=NOW,
        ))
    # u-member：会员有效 + 2 有效订阅 + 1 过期订阅
    s.add(orm.MembershipModel(
        user_id="u-member", tier="member", status="active", valid_until=FUTURE,
        is_permanent=False, version=1, created_at=NOW, updated_at=NOW,
    ))
    s.add(orm.StrategySubscriptionModel(
        user_id="u-member", strategy_id="strat-b", status="active", valid_until=FUTURE,
        is_permanent=False, version=1, created_at=NOW, updated_at=NOW,
    ))
    s.add(orm.StrategySubscriptionModel(
        user_id="u-member", strategy_id="strat-a", status="active", valid_until=None,
        is_permanent=True, version=1, created_at=NOW, updated_at=NOW,
    ))
    s.add(orm.StrategySubscriptionModel(
        user_id="u-member", strategy_id="strat-c", status="expired", valid_until=PAST,
        is_permanent=False, version=2, created_at=NOW, updated_at=NOW,
    ))
    # u-revoked：会员已撤销
    s.add(orm.MembershipModel(
        user_id="u-revoked", tier="member", status="revoked", valid_until=FUTURE,
        is_permanent=False, revoked_at=NOW, revoked_by_user_id="admin",
        version=2, created_at=NOW, updated_at=NOW,
    ))
    # u-member 一笔 paid-未发放订单（payment_grant_summary pending=1）
    s.add(orm.PaymentOrderModel(
        order_id="ord-1", user_id="u-member", product_type="membership",
        product_ref=None, months=12, amount_usdt=10, status="paid",
        paid_at=NOW, granted_at=None, created_at=NOW, updated_at=NOW,
    ))
    # 审计记录（recent_audits / audits 端点）
    for i, action in enumerate(("membership.set", "subscriptions.set")):
        s.add(orm.EntitlementAuditModel(
            audit_id=f"aud-{i}", operation_id=f"op-{i}", actor_user_id="admin",
            target_user_id="u-member", source_type="ADMIN", action=action,
            resource_type="MEMBERSHIP" if i == 0 else "SUBSCRIPTION",
            resource_id=None if i == 0 else "strat-b", reason="测试",
            before_state={}, after_state={"v": 1}, source_order_id=None,
            created_at=NOW - timedelta(minutes=10 - i),
        ))
    s.commit()
    s.close()

    import zhice_os_v2.api.dependencies as dep_mod
    import zhice_os_v2.application.services.admin_entitlement_service as svc_mod
    import zhice_os_v2.infrastructure.repositories.admin_entitlement_repository as repo_mod
    import zhice_os_v2.infrastructure.repositories.user_repository as urepo_mod

    monkeypatch.setattr(urepo_mod, "get_session", lambda: Session())
    monkeypatch.setattr(svc_mod, "get_session", lambda: Session())
    monkeypatch.setattr(repo_mod, "get_session", lambda: Session())
    monkeypatch.setattr(
        dep_mod, "get_settings",
        lambda: SimpleNamespace(persistence_backend="sql"),
    )

    from zhice_os_v2.api.main import app

    client = TestClient(app, raise_server_exceptions=True)
    return client, Session


def _token(user_id: str, role: str, auth_version: int = 1) -> str:
    from zhice_os_v2.application.services.token_service import create_token
    return create_token(user_id, user_id, role, auth_version)


def _admin_headers() -> dict:
    return {"Authorization": f"Bearer {_token('admin', 'ADMIN')}"}


# ── GET /admin/entitlements/users ────────────────────────────────────

def test_list_users_requires_admin(api_env):
    client, _ = api_env
    # VIEWER token → 403
    headers = {"Authorization": f"Bearer {_token('u-revoked', 'VIEWER')}"}
    r = client.get("/api/v1/admin/entitlements/users", headers=headers)
    assert r.status_code == 403


def test_list_users_paginates_and_orders(api_env):
    client, _ = api_env
    r = client.get("/api/v1/admin/entitlements/users", headers=_admin_headers())
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 4
    assert body["page"] == 1
    ids = [u["user_id"] for u in body["items"]]
    # created_at DESC：u-revoked(1d) > u-free(2d) > u-member(3d) > admin(10d)
    assert ids == ["u-revoked", "u-free", "u-member", "admin"]


def test_list_users_page_size_capped_at_100(api_env):
    client, _ = api_env
    r = client.get(
        "/api/v1/admin/entitlements/users?page_size=500", headers=_admin_headers()
    )
    assert r.status_code == 200
    assert r.json()["page_size"] == 100


def test_list_users_membership_realtime_and_active_sub_count(api_env):
    client, _ = api_env
    r = client.get(
        "/api/v1/admin/entitlements/users?query=active-member",
        headers=_admin_headers(),
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    u = items[0]
    assert u["user_id"] == "u-member"
    assert u["membership"]["effective_status"] == "active"
    # 有效订阅计数用实时谓词（永久 + 未过期），排除过期的 strat-c → 2
    assert u["active_subscription_count"] == 2


def test_list_users_free_and_revoked_status(api_env):
    client, _ = api_env
    r = client.get(
        "/api/v1/admin/entitlements/users?page_size=100", headers=_admin_headers()
    )
    by_id = {u["user_id"]: u for u in r.json()["items"]}
    assert by_id["u-free"]["membership"]["effective_status"] == "free"
    assert by_id["u-free"]["membership"]["version"] == 0
    assert by_id["u-revoked"]["membership"]["effective_status"] == "revoked"
    assert by_id["u-free"]["active_subscription_count"] == 0


# ── GET /admin/entitlements/users/{id} ───────────────────────────────

def test_user_detail_not_found(api_env):
    client, _ = api_env
    r = client.get(
        "/api/v1/admin/entitlements/users/nobody", headers=_admin_headers()
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "entitlements.user_not_found"


def test_user_detail_free_membership_projection(api_env):
    client, _ = api_env
    r = client.get(
        "/api/v1/admin/entitlements/users/u-free", headers=_admin_headers()
    )
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["user_id"] == "u-free"
    assert body["membership"]["effective_status"] == "free"
    assert body["membership"]["version"] == 0
    assert body["subscriptions"] == []


def test_user_detail_lists_all_subscription_states_ordered(api_env):
    client, _ = api_env
    r = client.get(
        "/api/v1/admin/entitlements/users/u-member", headers=_admin_headers()
    )
    body = r.json()
    subs = body["subscriptions"]
    # 含 active/permanent/expired 全部，按 strategy_id 排序
    assert [x["strategy_id"] for x in subs] == ["strat-a", "strat-b", "strat-c"]
    status_by = {x["strategy_id"]: x["effective_status"] for x in subs}
    assert status_by == {
        "strat-a": "permanent", "strat-b": "active", "strat-c": "expired"
    }
    # recent_audits ≤ 20，最新在前
    assert len(body["recent_audits"]) == 2
    assert body["recent_audits"][0]["action"] == "subscriptions.set"
    # payment_grant_summary：C 前用旧口径 paid 未发放 → pending 计数
    assert body["payment_grant_summary"]["pending"] == 1


# ── GET /admin/entitlements/audits ───────────────────────────────────

def test_audits_paginates(api_env):
    client, _ = api_env
    r = client.get(
        "/api/v1/admin/entitlements/audits", headers=_admin_headers()
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert body["items"][0]["action"] == "subscriptions.set"


def test_audits_filter_by_target(api_env):
    client, _ = api_env
    r = client.get(
        "/api/v1/admin/entitlements/audits?target_user_id=u-member",
        headers=_admin_headers(),
    )
    assert r.status_code == 200
    assert r.json()["total"] == 2


def test_audits_page_size_capped_at_200(api_env):
    client, _ = api_env
    r = client.get(
        "/api/v1/admin/entitlements/audits?page_size=999", headers=_admin_headers()
    )
    assert r.json()["page_size"] == 200


def test_audits_naive_datetime_rejected_422(api_env):
    client, _ = api_env
    r = client.get(
        "/api/v1/admin/entitlements/audits?from_at=2026-09-10T00:00:00",
        headers=_admin_headers(),
    )
    assert r.status_code == 422


def test_audits_requires_admin(api_env):
    client, _ = api_env
    headers = {"Authorization": f"Bearer {_token('u-free', 'USER')}"}
    r = client.get("/api/v1/admin/entitlements/audits", headers=headers)
    assert r.status_code == 403
