"""单元 B Task B1-9：角色变更与旧 grant 接入审计事务（服务层）。

change_role（复用 A 锁序 + ROLE audit + operation + auth_version+1）；
grant_membership_months / grant_subscription_months（月度叠加 + 资格谓词 +
永久收月费 no-op 不降级）。隔离内存 sqlite。
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.application.services.admin_entitlement_service import (
    AdminEntitlementService,
    EntitlementError,
)
from zhice_os_v2.domain.billing.admin_entitlement import AdminWriteContext
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.base import Base

NOW = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)


@pytest.fixture()
def env():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    s.add(orm.UserModel(user_id="admin", username="admin", password_hash="x",
                        role="ADMIN", is_active=True, auth_version=1, created_at=NOW))
    s.add(orm.UserModel(user_id="admin2", username="admin2", password_hash="x",
                        role="ADMIN", is_active=True, auth_version=1, created_at=NOW))
    s.add(orm.UserModel(user_id="u-t", username="t", password_hash="x", role="USER",
                        is_active=True, auth_version=1, created_at=NOW))
    s.add(orm.StrategyModel(
        strategy_id="strat-a", strategy_name="A", market="US", status="ACTIVE",
        owner_user_id=None, visibility="PUBLIC", publication_status="PUBLISHED",
        created_at=NOW, updated_at=NOW,
    ))
    s.add(orm.StrategyModel(
        strategy_id="strat-priv", strategy_name="P", market="US", status="ACTIVE",
        owner_user_id="x", visibility="PRIVATE", publication_status="PRIVATE",
        created_at=NOW, updated_at=NOW,
    ))
    s.commit()
    s.close()
    svc = AdminEntitlementService(
        session_factory=lambda: Session(),
        enabled_markets=frozenset({"US"}),
        clock=lambda: NOW,
    )
    return Session, svc


def _ctx(key="key-00000001", fp="fp-1"):
    return AdminWriteContext(actor_user_id="admin", idempotency_key=key,
                             request_fingerprint=fp)


def _counts(Session):
    s = Session()
    try:
        return (s.query(orm.AdminOperationModel).count(),
                s.query(orm.EntitlementAuditModel).count())
    finally:
        s.close()


def _auth_version(Session, uid):
    s = Session()
    try:
        return s.query(orm.UserModel).filter_by(user_id=uid).one().auth_version
    finally:
        s.close()


# ── change_role ──────────────────────────────────────────────────────

def test_change_role_bumps_version_and_audits(env):
    Session, svc = env
    r = svc.change_role(context=_ctx(), target_user_id="u-t", new_role="TRADER",
                        reason="晋升", expected_auth_version=1)
    assert r.status_code == 200
    assert r.body["role"] == "TRADER"
    assert r.body["auth_version"] == 2
    assert _auth_version(Session, "u-t") == 2
    assert _counts(Session) == (1, 1)


def test_change_role_replay_no_double_bump(env):
    Session, svc = env
    ctx = _ctx(key="dup-key-0001", fp="same")
    r1 = svc.change_role(context=ctx, target_user_id="u-t", new_role="TRADER",
                         reason="x", expected_auth_version=1)
    r2 = svc.change_role(context=ctx, target_user_id="u-t", new_role="TRADER",
                         reason="x", expected_auth_version=1)
    assert r1.body == r2.body
    assert r2.replayed is True
    assert _auth_version(Session, "u-t") == 2  # 只递增一次
    assert _counts(Session) == (1, 1)


def test_change_role_same_key_diff_fingerprint_conflict(env):
    _, svc = env
    svc.change_role(context=_ctx("k-1234567890", "fpA"), target_user_id="u-t",
                    new_role="TRADER", reason="x", expected_auth_version=1)
    with pytest.raises(EntitlementError) as e:
        svc.change_role(context=_ctx("k-1234567890", "fpB"), target_user_id="u-t",
                        new_role="VIEWER", reason="x", expected_auth_version=1)
    assert e.value.code == "entitlements.idempotency_conflict"


def test_change_role_version_conflict(env):
    _, svc = env
    with pytest.raises(EntitlementError) as e:
        svc.change_role(context=_ctx(), target_user_id="u-t", new_role="TRADER",
                        reason="x", expected_auth_version=99)
    assert e.value.code == "auth.auth_version_conflict"


def test_change_role_user_not_found(env):
    _, svc = env
    with pytest.raises(EntitlementError) as e:
        svc.change_role(context=_ctx(), target_user_id="ghost", new_role="TRADER",
                        reason="x", expected_auth_version=1)
    assert e.value.code == "auth.user_not_found"


def test_change_role_last_admin_protected_no_audit(env):
    Session, svc = env
    # 先降 admin2 → 只剩 admin 一个管理员
    svc.change_role(context=_ctx("k-demote0001", "f1"), target_user_id="admin2",
                    new_role="USER", reason="x", expected_auth_version=1)
    ops_before, auds_before = _counts(Session)
    with pytest.raises(EntitlementError) as e:
        svc.change_role(context=_ctx("k-lastadmin1", "f2"), target_user_id="admin",
                        new_role="USER", reason="x", expected_auth_version=1)
    assert e.value.code == "auth.last_admin_protected"
    # 拒绝不写 audit/operation
    assert _counts(Session) == (ops_before, auds_before)


# ── grant_membership_months ──────────────────────────────────────────

def test_grant_membership_months_creates_dated_member(env):
    Session, svc = env
    r = svc.grant_membership_months(context=_ctx(), target_user_id="u-t", months=3,
                                    reason="补单", expected_version=0)
    assert r.status_code == 200
    assert r.body["membership"]["effective_status"] == "active"
    assert r.body["membership"]["version"] == 1
    assert _counts(Session) == (1, 1)


def test_grant_membership_permanent_noop(env):
    Session, svc = env
    # 先设永久会员
    svc.set_membership(context=_ctx("k-perm00001", "fp-perm"), target_user_id="u-t",
                       mode="permanent", valid_until=None, reason="永久",
                       expected_version=0)
    # 再收月费 → no-op 不降级
    r = svc.grant_membership_months(context=_ctx("k-month0001", "fp-month"),
                                    target_user_id="u-t", months=1, reason="月费",
                                    expected_version=1)
    assert r.body["membership"]["is_permanent"] is True
    assert r.body["membership"]["effective_status"] == "permanent"


def test_grant_membership_version_conflict(env):
    _, svc = env
    with pytest.raises(EntitlementError) as e:
        svc.grant_membership_months(context=_ctx(), target_user_id="u-t", months=1,
                                    reason="x", expected_version=7)
    assert e.value.code == "entitlements.version_conflict"


# ── grant_subscription_months ────────────────────────────────────────

def test_grant_subscription_months_success(env):
    Session, svc = env
    r = svc.grant_subscription_months(context=_ctx(), target_user_id="u-t",
                                      strategy_id="strat-a", months=2, reason="补单",
                                      expected_version=0)
    assert r.status_code == 200
    assert r.body["subscription"]["effective_status"] == "active"
    assert _counts(Session) == (1, 1)


def test_grant_subscription_not_grantable(env):
    _, svc = env
    with pytest.raises(EntitlementError) as e:
        svc.grant_subscription_months(context=_ctx(), target_user_id="u-t",
                                      strategy_id="strat-priv", months=1, reason="x",
                                      expected_version=0)
    assert e.value.code == "entitlements.strategy_not_grantable"


# ── 端点接线（auth 改角色 / admin_billing 补单 经审计事务） ───────────
from types import SimpleNamespace  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from zhice_os_v2.domain.security.password import hash_password  # noqa: E402


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
    s.add(orm.UserModel(user_id="admin", username="admin",
                        password_hash=hash_password("123456"), role="ADMIN",
                        is_active=True, auth_version=1, created_at=NOW))
    s.add(orm.UserModel(user_id="u-t", username="t", password_hash="x", role="USER",
                        is_active=True, auth_version=1, created_at=NOW))
    s.add(orm.StrategyModel(
        strategy_id="strat-a", strategy_name="A", market="US", status="ACTIVE",
        owner_user_id=None, visibility="PUBLIC", publication_status="PUBLISHED",
        created_at=NOW, updated_at=NOW,
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
    monkeypatch.setattr(
        svc_mod, "get_settings",
        lambda: SimpleNamespace(scheduler_markets="US"), raising=False,
    )
    from zhice_os_v2.api.main import app
    return TestClient(app, raise_server_exceptions=True), Session


def _admin_auth() -> dict:
    from zhice_os_v2.application.services.token_service import create_token
    return {"Authorization": f"Bearer {create_token('admin', 'admin', 'ADMIN', 1)}"}


def test_endpoint_change_role_writes_audit_and_bumps(api_env):
    client, Session = api_env
    r = client.put("/api/v1/auth/users/u-t/role", headers=_admin_auth(),
                   json={"role": "TRADER", "auth_version": 1, "reason": "晋升"})
    assert r.status_code == 200
    assert r.json()["role"] == "TRADER" and r.json()["auth_version"] == 2
    s = Session()
    try:
        assert s.query(orm.EntitlementAuditModel).filter_by(
            resource_type="ROLE").count() == 1
    finally:
        s.close()


def test_endpoint_change_role_stale_version_409(api_env):
    client, _ = api_env
    r = client.put("/api/v1/auth/users/u-t/role", headers=_admin_auth(),
                   json={"role": "TRADER", "auth_version": 9, "reason": "x"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "auth.auth_version_conflict"


def test_endpoint_grant_membership_requires_idempotency_key(api_env):
    client, _ = api_env
    r = client.post("/api/v1/admin/billing/grant-membership", headers=_admin_auth(),
                    json={"user_id": "u-t", "months": 3, "reason": "补单",
                          "version": 0})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "entitlements.invalid_idempotency_key"


def test_endpoint_grant_membership_routes_through_audit(api_env):
    client, Session = api_env
    headers = {**_admin_auth(), "Idempotency-Key": "grant-mem-0001"}
    r = client.post("/api/v1/admin/billing/grant-membership", headers=headers,
                    json={"user_id": "u-t", "months": 3, "reason": "补单",
                          "version": 0})
    assert r.status_code == 200
    assert r.json()["membership"]["effective_status"] == "active"
    s = Session()
    try:
        assert s.query(orm.EntitlementAuditModel).filter_by(
            resource_type="MEMBERSHIP").count() == 1
        assert s.query(orm.AdminOperationModel).count() == 1
    finally:
        s.close()


def test_endpoint_grant_subscription_routes_through_audit(api_env):
    client, Session = api_env
    headers = {**_admin_auth(), "Idempotency-Key": "grant-sub-0001"}
    r = client.post("/api/v1/admin/billing/grant-subscription", headers=headers,
                    json={"user_id": "u-t", "strategy_id": "strat-a", "months": 2,
                          "reason": "补单", "version": 0})
    assert r.status_code == 200
    assert r.json()["subscription"]["effective_status"] == "active"
