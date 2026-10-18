"""C-3 订单级幂等原子发放：PaymentGrantService.grant_order + apply_grant_atomic。

隔离内存 sqlite：create_all 建全表，monkeypatch payment/membership 仓储 get_session
指向同一库（覆盖 conftest 的 _guard_real_pg）。发放语义复用 B1 月度叠加：
permanent 未撤销 no-op、expired/revoked 从 now、有效定期从原到期延长；支付永不改角色。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

pytestmark = pytest.mark.own_membership  # 用真实 MembershipService.is_member


@pytest.fixture()
def grant_env(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import zhice_os_v2.infrastructure.repositories.membership_repository as mem_mod
    import zhice_os_v2.infrastructure.repositories.payment_repository as pay_mod
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.base import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    for mod in (pay_mod, mem_mod):
        monkeypatch.setattr(mod, "get_session", lambda: Session())

    class _Env:
        def __init__(self):
            self.Session = Session
            self.orm = orm

        def make_user(self, user_id, *, role="USER", is_active=True):
            s = Session()
            s.add(
                orm.UserModel(
                    user_id=user_id,
                    username=user_id,
                    password_hash="x",
                    role=role,
                    is_active=is_active,
                    created_at=datetime.now(UTC),
                )
            )
            s.commit()
            s.close()

        def make_paid_order(
            self,
            order_id,
            user_id,
            *,
            product_type="membership",
            product_ref=None,
            months=1,
            grant_status="pending",
            historical_ambiguous=False,
        ):
            s = Session()
            now = datetime.now(UTC)
            s.add(
                orm.PaymentOrderModel(
                    order_id=order_id,
                    user_id=user_id,
                    product_type=product_type,
                    product_ref=product_ref,
                    months=months,
                    amount_usdt=Decimal("9.90000000"),
                    trade_type="usdt.trc20",
                    status="paid",
                    actual_amount=Decimal("9.90000000"),
                    paid_at=now,
                    grant_status=grant_status,
                    next_grant_retry_at=now,
                    historical_grant_ambiguous=historical_ambiguous,
                    created_at=now,
                    updated_at=now,
                )
            )
            s.commit()
            s.close()

        def order(self, order_id):
            from zhice_os_v2.infrastructure.repositories.payment_repository import (
                PaymentRepository,
            )

            return PaymentRepository().get_by_order_id(order_id)

        def membership_row(self, user_id):
            s = Session()
            try:
                return (
                    s.query(orm.MembershipModel)
                    .filter_by(user_id=user_id)
                    .first()
                )
            finally:
                s.close()

        def subscription_row(self, user_id, strategy_id):
            s = Session()
            try:
                return (
                    s.query(orm.StrategySubscriptionModel)
                    .filter_by(user_id=user_id, strategy_id=strategy_id)
                    .first()
                )
            finally:
                s.close()

        def user_role(self, user_id):
            s = Session()
            try:
                return (
                    s.query(orm.UserModel).filter_by(user_id=user_id).one().role
                )
            finally:
                s.close()

        def audits(self, order_id=None):
            s = Session()
            try:
                q = s.query(orm.EntitlementAuditModel)
                if order_id is not None:
                    q = q.filter_by(source_order_id=order_id)
                return q.all()
            finally:
                s.close()

    return _Env()


def _service():
    from zhice_os_v2.application.services.payment_grant_service import (
        PaymentGrantService,
    )

    return PaymentGrantService()


# ---------------- 会员单：只改会员，不动角色 ----------------
def test_grant_membership_order_activates_membership(grant_env):
    from zhice_os_v2.application.services.membership_service import MembershipService

    grant_env.make_user("u1", role="USER")
    grant_env.make_paid_order("ord-mem", "u1", product_type="membership")

    result = _service().grant_order(grant_env.order("ord-mem"))

    assert result == "granted"
    assert MembershipService().is_member("u1") is True
    assert grant_env.user_role("u1") == "USER"  # 支付永不改角色
    o = grant_env.order("ord-mem")
    assert o.grant_status == "granted"
    assert o.granted_at is not None
    assert o.grant_lease_owner is None  # 成功清租约


# ---------------- 订阅单：只改指定订阅 ----------------
def test_grant_subscription_order_activates_only_that_subscription(grant_env):
    from zhice_os_v2.application.services.membership_service import MembershipService

    grant_env.make_user("u2", role="USER")
    grant_env.make_paid_order(
        "ord-sub", "u2", product_type="subscription", product_ref="strat-A", months=2
    )

    result = _service().grant_order(grant_env.order("ord-sub"))

    assert result == "granted"
    sub = grant_env.subscription_row("u2", "strat-A")
    assert sub is not None and sub.status == "active"
    # 订阅单不触发会员：无会员行（或仍 free）。
    assert MembershipService().is_member("u2") is False
    assert grant_env.user_role("u2") == "USER"


# ---------------- 幂等：重复发放只发一次 ----------------
def test_grant_is_idempotent_no_double_extend(grant_env):
    grant_env.make_user("u3", role="USER")
    grant_env.make_paid_order("ord-idem", "u3", product_type="membership")

    svc = _service()
    assert svc.grant_order(grant_env.order("ord-idem")) == "granted"
    vu1 = grant_env.membership_row("u3").valid_until

    # 第二次：已 granted → 幂等，不重复叠加、不重复写 audit。
    assert svc.grant_order(grant_env.order("ord-idem")) == "already_granted"
    vu2 = grant_env.membership_row("u3").valid_until
    assert vu1 == vu2
    assert len(grant_env.audits("ord-idem")) == 1


# ---------------- permanent 收月费单：granted 但不降级 ----------------
def test_permanent_membership_not_downgraded(grant_env):
    grant_env.make_user("u4", role="USER")
    # 先置永久会员。
    s = grant_env.Session()
    now = datetime.now(UTC)
    s.add(
        grant_env.orm.MembershipModel(
            user_id="u4", tier="member", status="active", is_permanent=True,
            valid_until=None, version=1, created_at=now, updated_at=now,
        )
    )
    s.commit()
    s.close()
    grant_env.make_paid_order("ord-perm", "u4", product_type="membership")

    assert _service().grant_order(grant_env.order("ord-perm")) == "granted"
    m = grant_env.membership_row("u4")
    assert m.is_permanent is True  # 不降级
    assert m.valid_until is None
    assert grant_env.order("ord-perm").grant_status == "granted"  # 订单仍标 granted


# ---------------- inactive 用户仍履约 ----------------
def test_inactive_user_still_fulfilled(grant_env):
    grant_env.make_user("u5", role="USER", is_active=False)
    grant_env.make_paid_order("ord-inact", "u5", product_type="membership")

    assert _service().grant_order(grant_env.order("ord-inact")) == "granted"
    m = grant_env.membership_row("u5")
    assert m is not None and m.tier == "member" and m.status == "active"


# ---------------- expired 会员：续费从 now 起算 ----------------
def test_expired_membership_renewed_from_now(grant_env):
    grant_env.make_user("u6", role="USER")
    now = datetime.now(UTC)
    s = grant_env.Session()
    s.add(
        grant_env.orm.MembershipModel(
            user_id="u6", tier="member", status="active", is_permanent=False,
            valid_until=now - timedelta(days=10), version=1,
            created_at=now, updated_at=now,
        )
    )
    s.commit()
    s.close()
    grant_env.make_paid_order("ord-exp", "u6", product_type="membership", months=1)

    assert _service().grant_order(grant_env.order("ord-exp")) == "granted"
    vu = grant_env.membership_row("u6").valid_until
    if vu.tzinfo is None:
        vu = vu.replace(tzinfo=UTC)
    # 从 now 起算 ≈ now+30d（不是从旧到期 now-10d 叠加）。
    assert abs((vu - (now + timedelta(days=30))).total_seconds()) < 86400


# ---------------- 有效会员：续费从原到期延长 ----------------
def test_active_membership_extended_from_existing_expiry(grant_env):
    grant_env.make_user("u7", role="USER")
    now = datetime.now(UTC)
    existing = now + timedelta(days=100)
    s = grant_env.Session()
    s.add(
        grant_env.orm.MembershipModel(
            user_id="u7", tier="member", status="active", is_permanent=False,
            valid_until=existing, version=1, created_at=now, updated_at=now,
        )
    )
    s.commit()
    s.close()
    grant_env.make_paid_order("ord-ext", "u7", product_type="membership", months=1)

    assert _service().grant_order(grant_env.order("ord-ext")) == "granted"
    vu = grant_env.membership_row("u7").valid_until
    if vu.tzinfo is None:
        vu = vu.replace(tzinfo=UTC)
    # 从原到期 now+100d 延长 30d ≈ now+130d（远大于从 now 起算的 now+30d）。
    assert vu > now + timedelta(days=125)


# ---------------- revoked 会员：续费清撤销、从 now 起算 ----------------
def test_revoked_membership_renewed_and_cleared(grant_env):
    grant_env.make_user("u8", role="USER")
    now = datetime.now(UTC)
    s = grant_env.Session()
    s.add(
        grant_env.orm.MembershipModel(
            user_id="u8", tier="member", status="revoked", is_permanent=False,
            valid_until=now + timedelta(days=50),
            revoked_at=now - timedelta(days=1), revoked_by_user_id="admin",
            version=2, created_at=now, updated_at=now,
        )
    )
    s.commit()
    s.close()
    grant_env.make_paid_order("ord-rev", "u8", product_type="membership", months=1)

    assert _service().grant_order(grant_env.order("ord-rev")) == "granted"
    m = grant_env.membership_row("u8")
    assert m.revoked_at is None and m.status == "active"
    vu = m.valid_until.replace(tzinfo=UTC) if m.valid_until.tzinfo is None else m.valid_until
    assert abs((vu - (now + timedelta(days=30))).total_seconds()) < 86400


# ---------------- 发放写 PAYMENT 审计 ----------------
def test_grant_writes_payment_audit(grant_env):
    grant_env.make_user("u9", role="USER")
    grant_env.make_paid_order("ord-aud", "u9", product_type="membership")

    _service().grant_order(grant_env.order("ord-aud"))

    rows = grant_env.audits("ord-aud")
    assert len(rows) == 1
    a = rows[0]
    assert a.source_type == "PAYMENT"
    assert a.source_order_id == "ord-aud"
    assert a.resource_type == "MEMBERSHIP"
    assert a.target_user_id == "u9"
    assert a.actor_user_id is None
    assert a.operation_id is None


# ---------------- 历史歧义订单：不被自动发放 ----------------
def test_historical_ambiguous_order_not_auto_granted(grant_env):
    from zhice_os_v2.application.services.membership_service import MembershipService

    grant_env.make_user("u10", role="USER")
    grant_env.make_paid_order(
        "ord-amb", "u10", product_type="membership",
        grant_status="manual_review", historical_ambiguous=True,
    )

    result = _service().grant_order(grant_env.order("ord-amb"))

    assert result == "manual_review_required"
    assert MembershipService().is_member("u10") is False
    assert grant_env.membership_row("u10") is None
    assert grant_env.audits("ord-amb") == []


# ---------------- 非 paid 订单：拒绝发放 ----------------
def test_non_paid_order_rejected(grant_env):
    grant_env.make_user("u11", role="USER")
    s = grant_env.Session()
    now = datetime.now(UTC)
    s.add(
        grant_env.orm.PaymentOrderModel(
            order_id="ord-pending", user_id="u11", product_type="membership",
            product_ref=None, months=1, amount_usdt=Decimal("9.90000000"),
            trade_type="usdt.trc20", status="pending", grant_status="pending",
            created_at=now, updated_at=now,
        )
    )
    s.commit()
    s.close()

    assert _service().grant_order(grant_env.order("ord-pending")) == "bad_state"


# ---------------- 发放失败：置 retrying、不抛、原子回滚 ----------------
def test_grant_failure_marks_retrying_and_rolls_back(grant_env, monkeypatch):
    from zhice_os_v2.application.services import payment_grant_service as mod

    grant_env.make_user("u12", role="USER")
    grant_env.make_paid_order("ord-fail", "u12", product_type="membership")

    def _boom(self, s, order, now):
        raise RuntimeError("grant blew up")

    monkeypatch.setattr(mod.PaymentGrantService, "_grant_in_session", _boom)

    result = _service().grant_order(grant_env.order("ord-fail"))

    assert result == "retrying"
    o = grant_env.order("ord-fail")
    assert o.grant_status == "retrying"
    assert o.granted_at is None
    assert o.last_grant_error_code is not None
    assert grant_env.membership_row("u12") is None  # 权益写入已回滚
    assert grant_env.audits("ord-fail") == []
