"""C-4 支付发放 worker：PaymentGrantService.process_due_grants 认领 + 退避 + 崩溃回收。

隔离内存 sqlite（同 test_payment_grant_service）：claim_next_grant 认领 due 的 paid 订单，
apply_grant_atomic 发权益；发放异常按 [1,2,5,10,30,60,180,360]min 退避重试，第 8 次自动
尝试失败 → grant_status=failed + 告警管理员。processing 且租约过期可被回收重领。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

pytestmark = pytest.mark.own_membership


@pytest.fixture()
def env(monkeypatch):
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
                    user_id=user_id, username=user_id, password_hash="x",
                    role=role, is_active=is_active, created_at=datetime.now(UTC),
                )
            )
            s.commit()
            s.close()

        def make_paid_order(
            self, order_id, user_id, *, product_type="membership",
            product_ref=None, months=1, grant_status="pending",
            historical_ambiguous=False, next_retry_at=None,
            automatic_attempts=0, grant_attempts=0,
            lease_owner=None, lease_until=None, paid_at=None,
        ):
            s = Session()
            now = datetime.now(UTC)
            s.add(
                orm.PaymentOrderModel(
                    order_id=order_id, user_id=user_id, product_type=product_type,
                    product_ref=product_ref, months=months,
                    amount_usdt=Decimal("9.90000000"), trade_type="usdt.trc20",
                    status="paid", actual_amount=Decimal("9.90000000"),
                    paid_at=paid_at or now, grant_status=grant_status,
                    grant_attempts=grant_attempts,
                    automatic_grant_attempts=automatic_attempts,
                    next_grant_retry_at=now if next_retry_at is None else next_retry_at,
                    grant_lease_owner=lease_owner, grant_lease_until=lease_until,
                    historical_grant_ambiguous=historical_ambiguous,
                    created_at=now, updated_at=now,
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
                return s.query(orm.MembershipModel).filter_by(user_id=user_id).first()
            finally:
                s.close()

    return _Env()


def _service():
    from zhice_os_v2.application.services.payment_grant_service import (
        PaymentGrantService,
    )

    return PaymentGrantService()


def _boom(self, s, order, now):
    raise RuntimeError("grant blew up")


# ---------------- 正常：认领一个 due 的 paid 订单并发放 ----------------
def test_process_grants_due_paid_order(env):
    from zhice_os_v2.application.services.membership_service import MembershipService

    env.make_user("u1")
    env.make_paid_order("ord-due", "u1", product_type="membership")

    stats = _service().process_due_grants(lease_owner="w1")

    assert stats["claimed"] == 1
    assert stats["granted"] == 1
    assert MembershipService().is_member("u1") is True
    o = env.order("ord-due")
    assert o.grant_status == "granted"
    assert o.grant_lease_owner is None  # 成功清租约
    # 认领时自动尝试计数 +1。
    assert o.automatic_grant_attempts == 1


# ---------------- 尚未到期：next_grant_retry_at 在未来则不领 ----------------
def test_future_retry_not_claimed(env):
    env.make_user("u2")
    future = datetime.now(UTC) + timedelta(hours=1)
    env.make_paid_order("ord-future", "u2", grant_status="retrying", next_retry_at=future)

    stats = _service().process_due_grants(lease_owner="w1")

    assert stats["claimed"] == 0
    assert env.order("ord-future").grant_status == "retrying"


# ---------------- 历史歧义：绝不自动领取 ----------------
def test_historical_ambiguous_not_claimed(env):
    env.make_user("u3")
    env.make_paid_order(
        "ord-amb", "u3", grant_status="manual_review", historical_ambiguous=True,
    )

    stats = _service().process_due_grants(lease_owner="w1")

    assert stats["claimed"] == 0
    assert env.membership_row("u3") is None


# ---------------- 发放失败：退避 1min、置 retrying、不抛 ----------------
def test_failure_backs_off_one_minute(env, monkeypatch):
    from zhice_os_v2.application.services import payment_grant_service as mod

    env.make_user("u4")
    now = datetime(2026, 9, 11, 12, 0, 0, tzinfo=UTC)
    # next_retry_at 绑定测试 now（否则默认用真实墙钟，真实日期过 now 后订单"未到期"不被领）
    env.make_paid_order("ord-f1", "u4", next_retry_at=now)
    monkeypatch.setattr(mod.PaymentGrantService, "_grant_in_session", _boom)

    stats = _service().process_due_grants(lease_owner="w1", now=now)

    assert stats["claimed"] == 1
    assert stats["retrying"] == 1
    o = env.order("ord-f1")
    assert o.grant_status == "retrying"
    assert o.granted_at is None
    assert o.grant_lease_owner is None  # 失败清租约
    assert o.last_grant_error_code == "RuntimeError"
    nr = o.next_grant_retry_at
    nr = nr.replace(tzinfo=UTC) if nr.tzinfo is None else nr
    assert abs((nr - (now + timedelta(minutes=1))).total_seconds()) < 2


# ---------------- 退避阶梯：第 4 次自动尝试失败 → 排下次 10min ----------------
def test_backoff_schedule_fourth_attempt(env, monkeypatch):
    from zhice_os_v2.application.services import payment_grant_service as mod

    env.make_user("u5")
    now = datetime(2026, 9, 11, 12, 0, 0, tzinfo=UTC)
    # 已有 3 次自动尝试，本次认领 → 第 4 次；BACKOFF[3]=10min。
    # next_retry_at 绑定测试 now（否则默认用真实墙钟，真实日期过 now 后订单"未到期"不被领）
    env.make_paid_order("ord-f4", "u5", grant_status="retrying", automatic_attempts=3, next_retry_at=now)
    monkeypatch.setattr(mod.PaymentGrantService, "_grant_in_session", _boom)

    _service().process_due_grants(lease_owner="w1", now=now)

    o = env.order("ord-f4")
    assert o.automatic_grant_attempts == 4
    nr = o.next_grant_retry_at
    nr = nr.replace(tzinfo=UTC) if nr.tzinfo is None else nr
    assert abs((nr - (now + timedelta(minutes=10))).total_seconds()) < 2


# ---------------- 第 8 次失败 → failed + 告警 + 清 next_retry ----------------
def test_eighth_failure_marks_failed_and_alerts(env, monkeypatch):
    from zhice_os_v2.application.services import payment_grant_service as mod

    env.make_user("u6")
    env.make_paid_order("ord-f8", "u6", grant_status="retrying", automatic_attempts=7)
    monkeypatch.setattr(mod.PaymentGrantService, "_grant_in_session", _boom)
    alerts: list[tuple] = []
    monkeypatch.setattr(
        mod.alert_service, "send_alert",
        lambda level, title, body="", dedup_open=False: alerts.append((level, title)),
    )

    stats = _service().process_due_grants(lease_owner="w1")

    assert stats["failed"] == 1
    o = env.order("ord-f8")
    assert o.automatic_grant_attempts == 8
    assert o.grant_status == "failed"
    assert o.next_grant_retry_at is None  # 终态不再重试
    assert len(alerts) == 1
    assert "ord-f8" in alerts[0][1]


# ---------------- 崩溃回收：processing 且租约过期可被重领并发放 ----------------
def test_expired_lease_reclaimed(env):
    from zhice_os_v2.application.services.membership_service import MembershipService

    env.make_user("u7")
    past = datetime.now(UTC) - timedelta(minutes=10)
    env.make_paid_order(
        "ord-crash", "u7", grant_status="processing",
        lease_owner="dead-worker", lease_until=past, automatic_attempts=2,
    )

    stats = _service().process_due_grants(lease_owner="w2")

    assert stats["claimed"] == 1
    assert stats["granted"] == 1
    assert MembershipService().is_member("u7") is True
    o = env.order("ord-crash")
    assert o.grant_status == "granted"
    assert o.automatic_grant_attempts == 3  # 回收再尝试 +1


# ---------------- processing 但租约未过期：不被抢（活 worker 持锁）----------------
def test_processing_with_valid_lease_not_claimed(env):
    env.make_user("u8")
    future = datetime.now(UTC) + timedelta(minutes=5)
    env.make_paid_order(
        "ord-live", "u8", grant_status="processing",
        lease_owner="alive-worker", lease_until=future,
    )

    stats = _service().process_due_grants(lease_owner="w2")

    assert stats["claimed"] == 0
    assert env.order("ord-live").grant_status == "processing"


# ---------------- 多单一轮全部认领 ----------------
def test_processes_all_due_in_one_pass(env):
    env.make_user("a")
    env.make_user("b")
    env.make_paid_order("ord-a", "a")
    env.make_paid_order("ord-b", "b")

    stats = _service().process_due_grants(lease_owner="w1")

    assert stats["claimed"] == 2
    assert stats["granted"] == 2
