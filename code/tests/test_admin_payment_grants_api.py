"""C-5 管理员支付发放管理 API：GET/POST /admin/entitlements/payment-grants。

隔离内存 sqlite；ADMIN 身份用 create_token 直发（写方法经中间件，ADMIN 放行）。
覆盖：列表默认 paid 未 granted + 筛选、retry 状态门（failed 发放 / granted 返当前 /
歧义 409 grant_review_required / processing 活租约 409 in_progress / 缺失 404）、
review（仅历史歧义 MARK_GRANTED 标记不发权益 / RETRY 转 pending / 非歧义 409）、
非 ADMIN 403。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
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
    s.commit()
    s.close()

    import zhice_os_v2.api.dependencies as dep_mod
    import zhice_os_v2.application.services.membership_service as memsvc_mod
    import zhice_os_v2.infrastructure.repositories.membership_repository as memrepo_mod
    import zhice_os_v2.infrastructure.repositories.payment_repository as pay_mod
    import zhice_os_v2.infrastructure.repositories.user_repository as urepo_mod

    for mod in (urepo_mod, pay_mod, memrepo_mod):
        monkeypatch.setattr(mod, "get_session", lambda: Session())
    monkeypatch.setattr(
        memsvc_mod, "get_session", lambda: Session(), raising=False
    )
    monkeypatch.setattr(
        dep_mod, "get_settings",
        lambda: SimpleNamespace(persistence_backend="sql"),
    )

    from zhice_os_v2.api.main import app

    client = TestClient(app, raise_server_exceptions=True)
    return client, Session


def _token(user_id: str, role: str) -> str:
    from zhice_os_v2.application.services.token_service import create_token
    return create_token(user_id, user_id, role, 1)


def _h(role: str = "ADMIN", user_id: str = "admin", key: str = "idem-pg-0001") -> dict:
    return {
        "Authorization": f"Bearer {_token(user_id, role)}",
        "Idempotency-Key": key,
    }


def _order(
    Session, order_id, *, user_id="u-t", product_type="membership",
    product_ref=None, grant_status="pending", historical_ambiguous=False,
    lease_owner=None, lease_until=None, automatic_attempts=0,
    last_error_code=None, granted_at=None, paid_at=None,
):
    s = Session()
    now = datetime.now(UTC)
    s.add(orm.PaymentOrderModel(
        order_id=order_id, user_id=user_id, product_type=product_type,
        product_ref=product_ref, months=1, amount_usdt=Decimal("9.90000000"),
        trade_type="usdt.trc20", status="paid", actual_amount=Decimal("9.90000000"),
        paid_at=paid_at or now, grant_status=grant_status,
        automatic_grant_attempts=automatic_attempts,
        next_grant_retry_at=now, last_grant_error_code=last_error_code,
        grant_lease_owner=lease_owner, grant_lease_until=lease_until,
        historical_grant_ambiguous=historical_ambiguous, granted_at=granted_at,
        created_at=now, updated_at=now,
    ))
    s.commit()
    s.close()


def _membership_row(Session, user_id):
    s = Session()
    try:
        return s.query(orm.MembershipModel).filter_by(user_id=user_id).first()
    finally:
        s.close()


# ── list ─────────────────────────────────────────────────────────────
def test_list_defaults_to_paid_not_granted(api_env):
    client, Session = api_env
    _order(Session, "ord-pending", grant_status="pending")
    _order(Session, "ord-failed", grant_status="failed", last_error_code="Boom")
    _order(Session, "ord-granted", grant_status="granted", granted_at=NOW)

    r = client.get("/api/v1/admin/entitlements/payment-grants", headers=_h())

    assert r.status_code == 200
    ids = {it["order_id"] for it in r.json()["items"]}
    assert ids == {"ord-pending", "ord-failed"}  # granted 默认不列
    assert r.json()["total"] == 2


def test_list_filter_by_grant_status(api_env):
    client, Session = api_env
    _order(Session, "ord-pending", grant_status="pending")
    _order(Session, "ord-failed", grant_status="failed")

    r = client.get(
        "/api/v1/admin/entitlements/payment-grants?grant_status=failed", headers=_h()
    )

    assert r.status_code == 200
    assert [it["order_id"] for it in r.json()["items"]] == ["ord-failed"]


def test_list_filter_by_user(api_env):
    client, Session = api_env
    _order(Session, "ord-1", user_id="u-t")
    s = Session()
    s.add(orm.UserModel(
        user_id="u-2", username="u2", password_hash="x", role="USER",
        is_active=True, auth_version=1, created_at=NOW,
    ))
    s.commit()
    s.close()
    _order(Session, "ord-2", user_id="u-2")

    r = client.get(
        "/api/v1/admin/entitlements/payment-grants?user_id=u-2", headers=_h()
    )

    assert [it["order_id"] for it in r.json()["items"]] == ["ord-2"]


def test_list_requires_admin(api_env):
    client, Session = api_env
    r = client.get(
        "/api/v1/admin/entitlements/payment-grants",
        headers={"Authorization": f"Bearer {_token('u-t', 'USER')}"},
    )
    assert r.status_code == 403


# ── retry ────────────────────────────────────────────────────────────
def test_retry_failed_order_grants(api_env):
    client, Session = api_env
    _order(Session, "ord-f", grant_status="failed", last_error_code="Boom")

    r = client.post(
        "/api/v1/admin/entitlements/payment-grants/ord-f/retry",
        headers=_h(), json={"reason": "客服手动重试"},
    )

    assert r.status_code == 200
    assert r.json()["status"] == "granted"
    assert r.json()["order"]["grant_status"] == "granted"
    assert _membership_row(Session, "u-t") is not None


def test_retry_granted_order_returns_current(api_env):
    client, Session = api_env
    _order(Session, "ord-g", grant_status="granted", granted_at=NOW)

    r = client.post(
        "/api/v1/admin/entitlements/payment-grants/ord-g/retry",
        headers=_h(), json={"reason": "重试已发放"},
    )

    assert r.status_code == 200
    assert r.json()["status"] == "already_granted"
    # 不重复发放：无会员写入（此单从未真正发过权益）。
    assert _membership_row(Session, "u-t") is None


def test_retry_ambiguous_requires_review(api_env):
    client, Session = api_env
    _order(Session, "ord-amb", grant_status="manual_review", historical_ambiguous=True)

    r = client.post(
        "/api/v1/admin/entitlements/payment-grants/ord-amb/retry",
        headers=_h(), json={"reason": "重试歧义单"},
    )

    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "billing.grant_review_required"


def test_retry_processing_with_valid_lease_conflicts(api_env):
    client, Session = api_env
    future = datetime.now(UTC) + timedelta(minutes=5)
    _order(
        Session, "ord-live", grant_status="processing",
        lease_owner="worker-1", lease_until=future,
    )

    r = client.post(
        "/api/v1/admin/entitlements/payment-grants/ord-live/retry",
        headers=_h(), json={"reason": "抢占进行中"},
    )

    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "billing.grant_in_progress"


def test_retry_missing_order_404(api_env):
    client, _ = api_env
    r = client.post(
        "/api/v1/admin/entitlements/payment-grants/nope/retry",
        headers=_h(), json={"reason": "不存在"},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "billing.order_not_found"


def test_retry_requires_idempotency_key(api_env):
    client, Session = api_env
    _order(Session, "ord-f", grant_status="failed")
    r = client.post(
        "/api/v1/admin/entitlements/payment-grants/ord-f/retry",
        headers={"Authorization": f"Bearer {_token('admin', 'ADMIN')}"},
        json={"reason": "缺 key"},
    )
    assert r.status_code == 422


# ── review ───────────────────────────────────────────────────────────
def test_review_mark_granted_reconciles_without_granting(api_env):
    client, Session = api_env
    _order(Session, "ord-amb", grant_status="manual_review", historical_ambiguous=True)

    r = client.post(
        "/api/v1/admin/entitlements/payment-grants/ord-amb/review",
        headers=_h(), json={"decision": "MARK_GRANTED", "reason": "历史已开通，标记对账"},
    )

    assert r.status_code == 200
    o = r.json()["order"]
    assert o["grant_status"] == "granted"
    assert o["historical_grant_ambiguous"] is False
    assert o["grant_review_decision"] == "MARK_GRANTED"
    assert o["grant_reviewed_by_user_id"] == "admin"
    # MARK_GRANTED 只对账、不发权益（历史已开通）。
    assert _membership_row(Session, "u-t") is None


def test_review_retry_requeues_for_worker(api_env):
    client, Session = api_env
    _order(Session, "ord-amb", grant_status="manual_review", historical_ambiguous=True)

    r = client.post(
        "/api/v1/admin/entitlements/payment-grants/ord-amb/review",
        headers=_h(), json={"decision": "RETRY", "reason": "确认应发放，转重试"},
    )

    assert r.status_code == 200
    o = r.json()["order"]
    assert o["grant_status"] == "pending"
    assert o["historical_grant_ambiguous"] is False
    assert o["grant_review_decision"] == "RETRY"
    assert o["next_grant_retry_at"] is not None


def test_review_non_ambiguous_rejected(api_env):
    client, Session = api_env
    _order(Session, "ord-f", grant_status="failed")

    r = client.post(
        "/api/v1/admin/entitlements/payment-grants/ord-f/review",
        headers=_h(), json={"decision": "RETRY", "reason": "非歧义单不可复核"},
    )

    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "billing.not_reviewable"


def test_review_writes_audit(api_env):
    client, Session = api_env
    _order(Session, "ord-amb", grant_status="manual_review", historical_ambiguous=True)

    client.post(
        "/api/v1/admin/entitlements/payment-grants/ord-amb/review",
        headers=_h(), json={"decision": "MARK_GRANTED", "reason": "对账"},
    )

    s = Session()
    try:
        rows = (
            s.query(orm.EntitlementAuditModel)
            .filter_by(action="payment.review")
            .all()
        )
    finally:
        s.close()
    assert len(rows) == 1
    assert rows[0].actor_user_id == "admin"
    assert rows[0].target_user_id == "u-t"
