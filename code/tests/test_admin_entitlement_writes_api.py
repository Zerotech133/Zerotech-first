"""单元 B Task B1-8：管理员权益写 API（设置会员 / 批量订阅 / 撤销订阅）。

隔离内存 sqlite；ADMIN 身份用 create_token 直发（写方法经写限制中间件，ADMIN 放行）。
覆盖：成功、幂等复放（同 key 同指纹）、幂等冲突（同 key 异指纹 409）、版本冲突、
错误码→HTTP 映射、versions_mismatch、strategy_not_grantable、撤销缺失、
公开 membership/revoke 端点未注册（等 B2）。
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
FUTURE = (NOW + timedelta(days=365)).replace(microsecond=0)
FUTURE_ISO = FUTURE.isoformat()


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
    s.add(orm.UserModel(
        user_id="admin", username="admin", password_hash=hash_password("123456"),
        role="ADMIN", is_active=True, auth_version=1, created_at=NOW,
    ))
    s.add(orm.UserModel(
        user_id="u-t", username="target", password_hash="x", role="USER",
        is_active=True, auth_version=1, created_at=NOW,
    ))
    s.add(orm.UserModel(
        user_id="u-inactive", username="inact", password_hash="x", role="USER",
        is_active=False, auth_version=1, created_at=NOW,
    ))
    for sid in ("strat-a", "strat-b"):
        s.add(orm.StrategyModel(
            strategy_id=sid, strategy_name=sid, market="US", status="ACTIVE",
            owner_user_id=None, visibility="PUBLIC", publication_status="PUBLISHED",
            created_at=NOW, updated_at=NOW,
        ))
    # 一个私有策略（不可授权）
    s.add(orm.StrategyModel(
        strategy_id="strat-private", strategy_name="priv", market="US",
        status="ACTIVE", owner_user_id="someone", visibility="PRIVATE",
        publication_status="PRIVATE", created_at=NOW, updated_at=NOW,
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
    # 服务默认 enabled_markets 读 settings.scheduler_markets；这里显式含 US。
    monkeypatch.setattr(
        svc_mod, "get_settings",
        lambda: SimpleNamespace(scheduler_markets="US,CRYPTO"),
        raising=False,
    )

    from zhice_os_v2.api.main import app

    client = TestClient(app, raise_server_exceptions=True)
    return client, Session


def _token(user_id: str, role: str, auth_version: int = 1) -> str:
    from zhice_os_v2.application.services.token_service import create_token
    return create_token(user_id, user_id, role, auth_version)


def _h(key: str = "idem-key-0001") -> dict:
    return {
        "Authorization": f"Bearer {_token('admin', 'ADMIN')}",
        "Idempotency-Key": key,
    }


# ── PUT membership ───────────────────────────────────────────────────

def test_set_membership_until_success(api_env):
    client, _ = api_env
    r = client.put(
        "/api/v1/admin/entitlements/users/u-t/membership",
        headers=_h(),
        json={"mode": "until", "valid_until": FUTURE_ISO, "reason": "年费",
              "version": 0},
    )
    assert r.status_code == 200
    assert r.json()["membership"]["effective_status"] == "active"
    assert r.json()["membership"]["version"] == 1


def test_set_membership_idempotent_replay(api_env):
    client, _ = api_env
    body = {"mode": "permanent", "valid_until": None, "reason": "永久", "version": 0}
    r1 = client.put(
        "/api/v1/admin/entitlements/users/u-t/membership", headers=_h("dup-key-0001"),
        json=body,
    )
    r2 = client.put(
        "/api/v1/admin/entitlements/users/u-t/membership", headers=_h("dup-key-0001"),
        json=body,
    )
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json() == r2.json()
    # 只写一行版本 1（复放不再递增）
    assert r2.json()["membership"]["version"] == 1


def test_set_membership_same_key_diff_fingerprint_conflict(api_env):
    client, _ = api_env
    client.put(
        "/api/v1/admin/entitlements/users/u-t/membership", headers=_h("k-conflict01"),
        json={"mode": "permanent", "valid_until": None, "reason": "a", "version": 0},
    )
    r = client.put(
        "/api/v1/admin/entitlements/users/u-t/membership", headers=_h("k-conflict01"),
        json={"mode": "permanent", "valid_until": None, "reason": "b", "version": 0},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "entitlements.idempotency_conflict"


def test_set_membership_version_conflict(api_env):
    client, _ = api_env
    r = client.put(
        "/api/v1/admin/entitlements/users/u-t/membership", headers=_h(),
        json={"mode": "permanent", "valid_until": None, "reason": "x", "version": 5},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "entitlements.version_conflict"


def test_set_membership_user_not_found(api_env):
    client, _ = api_env
    r = client.put(
        "/api/v1/admin/entitlements/users/nobody/membership", headers=_h(),
        json={"mode": "permanent", "valid_until": None, "reason": "x", "version": 0},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "entitlements.user_not_found"


def test_set_membership_user_inactive_409(api_env):
    client, _ = api_env
    r = client.put(
        "/api/v1/admin/entitlements/users/u-inactive/membership", headers=_h(),
        json={"mode": "permanent", "valid_until": None, "reason": "x", "version": 0},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "entitlements.user_inactive"


def test_set_membership_invalid_key_422(api_env):
    client, _ = api_env
    r = client.put(
        "/api/v1/admin/entitlements/users/u-t/membership", headers=_h("short"),
        json={"mode": "permanent", "valid_until": None, "reason": "x", "version": 0},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "entitlements.invalid_idempotency_key"


def test_set_membership_empty_reason_422(api_env):
    client, _ = api_env
    r = client.put(
        "/api/v1/admin/entitlements/users/u-t/membership", headers=_h(),
        json={"mode": "permanent", "valid_until": None, "reason": "   ", "version": 0},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "entitlements.invalid_reason"


def test_set_membership_invalid_expiry_422(api_env):
    client, _ = api_env
    r = client.put(
        "/api/v1/admin/entitlements/users/u-t/membership", headers=_h(),
        json={"mode": "until", "valid_until": None, "reason": "x", "version": 0},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "entitlements.invalid_expiry"


# ── PUT subscriptions ────────────────────────────────────────────────

def test_set_subscriptions_batch_success(api_env):
    client, _ = api_env
    r = client.put(
        "/api/v1/admin/entitlements/users/u-t/subscriptions", headers=_h(),
        json={"strategy_ids": ["strat-a", "strat-b"], "mode": "until",
              "valid_until": FUTURE_ISO, "reason": "批量",
              "versions": {"strat-a": 0, "strat-b": 0}},
    )
    assert r.status_code == 200
    subs = r.json()["subscriptions"]
    assert {x["strategy_id"] for x in subs} == {"strat-a", "strat-b"}
    assert all(x["effective_status"] == "active" for x in subs)


def test_set_subscriptions_versions_mismatch_422(api_env):
    client, _ = api_env
    r = client.put(
        "/api/v1/admin/entitlements/users/u-t/subscriptions", headers=_h(),
        json={"strategy_ids": ["strat-a", "strat-b"], "mode": "permanent",
              "valid_until": None, "reason": "x", "versions": {"strat-a": 0}},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "entitlements.versions_mismatch"


def test_set_subscriptions_not_grantable_400(api_env):
    client, _ = api_env
    r = client.put(
        "/api/v1/admin/entitlements/users/u-t/subscriptions", headers=_h(),
        json={"strategy_ids": ["strat-private"], "mode": "permanent",
              "valid_until": None, "reason": "x", "versions": {"strat-private": 0}},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "entitlements.strategy_not_grantable"


# ── POST subscriptions/revoke ────────────────────────────────────────

def test_revoke_subscriptions_success(api_env):
    client, _ = api_env
    client.put(
        "/api/v1/admin/entitlements/users/u-t/subscriptions", headers=_h("grant-key01"),
        json={"strategy_ids": ["strat-a"], "mode": "permanent", "valid_until": None,
              "reason": "grant", "versions": {"strat-a": 0}},
    )
    r = client.post(
        "/api/v1/admin/entitlements/users/u-t/subscriptions/revoke",
        headers=_h("revoke-key01"),
        json={"strategy_ids": ["strat-a"], "reason": "撤销",
              "versions": {"strat-a": 1}},
    )
    assert r.status_code == 200
    assert r.json()["subscriptions"][0]["effective_status"] == "revoked"


def test_revoke_subscriptions_not_granted_409(api_env):
    client, _ = api_env
    r = client.post(
        "/api/v1/admin/entitlements/users/u-t/subscriptions/revoke", headers=_h(),
        json={"strategy_ids": ["strat-b"], "reason": "x", "versions": {"strat-b": 0}},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "entitlements.subscription_not_granted"


# ── POST membership/revoke（B2-4：撤销交易边界就绪后注册） ────────────

def test_revoke_membership_success_returns_cleanup(api_env):
    client, _ = api_env
    client.put(
        "/api/v1/admin/entitlements/users/u-t/membership", headers=_h("mem-grant01"),
        json={"mode": "permanent", "valid_until": None, "reason": "grant",
              "version": 0},
    )
    r = client.post(
        "/api/v1/admin/entitlements/users/u-t/membership/revoke",
        headers=_h("mem-revoke1"), json={"reason": "撤销", "version": 1},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["membership"]["effective_status"] == "revoked"
    cleanup = body["opening_risk_cleanup"]
    assert "disabled_instance_ids" in cleanup
    assert "released_amount" in cleanup
    assert "pending_external_cancellations" in cleanup


def test_revoke_membership_not_granted_409(api_env):
    client, _ = api_env
    r = client.post(
        "/api/v1/admin/entitlements/users/u-t/membership/revoke", headers=_h(),
        json={"reason": "x", "version": 0},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "entitlements.membership_not_granted"


def test_revoke_membership_requires_idempotency_key(api_env):
    client, _ = api_env
    headers = {"Authorization": f"Bearer {_token('admin', 'ADMIN')}"}
    r = client.post(
        "/api/v1/admin/entitlements/users/u-t/membership/revoke", headers=headers,
        json={"reason": "x", "version": 0},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "entitlements.invalid_idempotency_key"


def test_revoke_membership_version_conflict_409(api_env):
    client, _ = api_env
    # 授权会员后其 version=1；用陈旧 version=0 撤销 → 409 版本冲突。
    client.put(
        "/api/v1/admin/entitlements/users/u-t/membership", headers=_h("mem-grant02"),
        json={"mode": "permanent", "valid_until": None, "reason": "grant",
              "version": 0},
    )
    r = client.post(
        "/api/v1/admin/entitlements/users/u-t/membership/revoke",
        headers=_h("mem-revoke2"), json={"reason": "撤销", "version": 0},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "entitlements.version_conflict"


def test_revoke_membership_disables_self_built_instance(api_env):
    from decimal import Decimal
    client, Session = api_env
    # seed u-t 的自建私有策略 + 版本 + 账户 + 启用实例。
    s = Session()
    s.add(orm.StrategyModel(
        strategy_id="own-1", strategy_name="own-1", market="US", status="ACTIVE",
        owner_user_id="u-t", visibility="PRIVATE", publication_status="PRIVATE",
        created_at=NOW, updated_at=NOW,
    ))
    s.add(orm.StrategyVersionModel(
        strategy_version_id="ownv-1", strategy_id="own-1", version_no="1",
        market="US", status="ACTIVE", spec={}, created_at=NOW,
    ))
    s.add(orm.UserAccountModel(
        account_id="acc-r1", user_id="u-t", market="US", currency="USD",
        account_type="SHARED_PORTFOLIO", initial_capital=Decimal("100000"),
        cash_balance=Decimal("100000"), reserved_cash=Decimal("0"),
        status="ACTIVE", created_at=NOW, updated_at=NOW,
    ))
    s.add(orm.UserStrategyInstanceModel(
        user_strategy_id="usi-r1", user_id="u-t", strategy_version_id="ownv-1",
        account_id="acc-r1", enabled=True, auto_execute=False,
        execution_mode="MANUAL", max_position_ratio=Decimal("0.1"),
        created_at=NOW, updated_at=NOW,
    ))
    s.commit()
    s.close()

    client.put(
        "/api/v1/admin/entitlements/users/u-t/membership", headers=_h("g-key-0001"),
        json={"mode": "permanent", "valid_until": None, "reason": "g", "version": 0},
    )
    r = client.post(
        "/api/v1/admin/entitlements/users/u-t/membership/revoke",
        headers=_h("r-key-0001"), json={"reason": "撤销", "version": 1},
    )
    assert r.status_code == 200
    assert "usi-r1" in r.json()["opening_risk_cleanup"]["disabled_instance_ids"]
    s = Session()
    try:
        inst = s.query(orm.UserStrategyInstanceModel).filter_by(
            user_strategy_id="usi-r1").one()
        assert inst.enabled is False
    finally:
        s.close()


# ── 鉴权 ─────────────────────────────────────────────────────────────

def test_write_requires_admin(api_env):
    client, _ = api_env
    headers = {
        "Authorization": f"Bearer {_token('u-t', 'USER')}",
        "Idempotency-Key": "idem-key-0001",
    }
    r = client.put(
        "/api/v1/admin/entitlements/users/u-t/membership", headers=headers,
        json={"mode": "permanent", "valid_until": None, "reason": "x", "version": 0},
    )
    assert r.status_code == 403
