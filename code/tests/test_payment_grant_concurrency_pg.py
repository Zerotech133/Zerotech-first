"""单元 C Task C-7：支付权益发放并发正确性（PostgreSQL）。

整文件 @real_pg，需 REAL_PG=1 且真 PG（已迁移到 0031）。四场景验证 worker 与
管理员并发下的关键不变量（均为确定性断言，非 timing）：

- A 两 worker 认领同一到期订单：FOR UPDATE SKIP LOCKED → 恰一个领到，另一个 None；
- B 同订单并发原子发放：单行 FOR UPDATE 串行化 → 恰一次 granted、一次 already_granted，
  权益审计 source_order_id 恰一行（幂等键=order_id）；
- C processing 租约过期回收：两 worker 竞争回收 → 恰一个领到；
- D 管理员/支付并发写同用户会员：支付发放恰一次（audit==1），管理员乐观锁要么成功、
  要么 version_conflict（陈旧写被拒 = 无丢更新），最终会员有效、版本单调不损坏。

跑法（本机 Homebrew PG16，用完 dropdb）：
  createdb zhice_grant_cc_test
  DB_MAIN_URL=postgresql://zhice:zhice_pass@127.0.0.1:5432/zhice_grant_cc_test \
    alembic upgrade 0031
  REAL_PG=1 DB_MAIN_URL=postgresql://zhice:zhice_pass@127.0.0.1:5432/zhice_grant_cc_test \
    PERSISTENCE_BACKEND=sql PYTHONPATH=src .venv/bin/python -m pytest \
    -o addopts="" -m real_pg tests/test_payment_grant_concurrency_pg.py -q
  dropdb zhice_grant_cc_test
"""
from __future__ import annotations

import os
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from zhice_os_v2.application.services.admin_entitlement_service import (
    AdminEntitlementService,
    EntitlementError,
)
from zhice_os_v2.application.services.payment_grant_service import PaymentGrantService
from zhice_os_v2.domain.billing.admin_entitlement import AdminWriteContext
from zhice_os_v2.infrastructure.repositories.payment_repository import PaymentRepository

pytestmark = pytest.mark.real_pg


def _pg_available() -> bool:
    try:
        from sqlalchemy import text

        from zhice_os_v2.infrastructure.db.session import get_session

        s = get_session()
        try:
            s.execute(text("select 1"))
            return True
        finally:
            s.close()
    except Exception:
        return False


def _skip_unless_pg():
    if not (os.getenv("REAL_PG") == "1" and _pg_available()):
        pytest.skip("需要 REAL_PG=1 且真 PostgreSQL 可用（已迁移到 0031）")


@pytest.fixture()
def pg_env():
    _skip_unless_pg()
    from sqlalchemy import text

    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session

    sfx = datetime.now(UTC).strftime("%H%M%S%f")
    uid = f"zzpg_u_{sfx}"
    admin = f"zzpg_admin_{sfx}"
    sid = f"zzpg_s_{sfx}"
    now = datetime.now(UTC)

    s = get_session()
    try:
        s.add(orm.UserModel(user_id=uid, username=uid, password_hash="x",
                            role="USER", is_active=True, locale="zh", created_at=now))
        s.add(orm.UserModel(user_id=admin, username=admin, password_hash="x",
                            role="ADMIN", is_active=True, locale="zh", created_at=now))
        s.add(orm.StrategyModel(
            strategy_id=sid, strategy_name=sid, market="US", status="ACTIVE",
            owner_user_id=None, visibility="PUBLIC", publication_status="PUBLISHED",
            created_at=now, updated_at=now,
        ))
        s.flush()
        s.add(orm.MembershipModel(
            user_id=uid, tier="member", status="active",
            valid_until=now + timedelta(days=365), is_permanent=False, version=1,
            created_at=now, updated_at=now,
        ))
        s.commit()
    finally:
        s.close()

    def make_paid_order(
        oid, *, product_type="membership", product_ref=None, months=1,
        grant_status="pending", status="paid", lease_until=None, lease_owner=None,
        next_retry=None,
    ):
        so = get_session()
        try:
            so.add(orm.PaymentOrderModel(
                order_id=oid, user_id=uid,
                product_type=product_type, product_ref=product_ref,
                months=months, amount_usdt=Decimal("10.00000000"),
                trade_type="usdt.trc20", status=status,
                grant_status=grant_status,
                paid_at=now, next_grant_retry_at=(now if next_retry is None else next_retry),
                grant_lease_until=lease_until, grant_lease_owner=lease_owner,
                created_at=now, updated_at=now,
            ))
            so.commit()
        finally:
            so.close()

    def audit_count(oid: str) -> int:
        q = get_session()
        try:
            return int(q.execute(
                text("SELECT count(*) FROM v2_entitlement_audit WHERE source_order_id=:o"),
                {"o": oid},
            ).scalar_one())
        finally:
            q.close()

    def membership_row():
        q = get_session()
        try:
            return q.execute(
                text("SELECT status, version, is_permanent, valid_until, revoked_at "
                     "FROM v2_membership WHERE user_id=:u"),
                {"u": uid},
            ).mappings().one()
        finally:
            q.close()

    def grant_status_of(oid: str) -> str:
        q = get_session()
        try:
            return str(q.execute(
                text("SELECT grant_status FROM v2_payment_order WHERE order_id=:o"),
                {"o": oid},
            ).scalar_one())
        finally:
            q.close()

    ids = {
        "uid": uid, "admin": admin, "sid": sid, "now": now, "sfx": sfx,
        "make_paid_order": make_paid_order, "audit_count": audit_count,
        "membership_row": membership_row, "grant_status_of": grant_status_of,
    }
    yield ids

    cleanup = get_session()
    try:
        cleanup.execute(text("DELETE FROM v2_entitlement_audit WHERE target_user_id=:u"), {"u": uid})
        cleanup.execute(text("DELETE FROM v2_admin_operation WHERE target_user_id=:u OR actor_user_id=:a"),
                        {"u": uid, "a": admin})
        cleanup.execute(text("DELETE FROM v2_strategy_subscription WHERE user_id=:u"), {"u": uid})
        cleanup.execute(text("DELETE FROM v2_payment_order WHERE user_id=:u"), {"u": uid})
        cleanup.execute(text("DELETE FROM v2_membership WHERE user_id=:u"), {"u": uid})
        cleanup.execute(text("DELETE FROM v2_strategy WHERE strategy_id=:s"), {"s": sid})
        cleanup.execute(text("DELETE FROM v2_user WHERE user_id IN (:u, :a)"), {"u": uid, "a": admin})
        cleanup.commit()
    finally:
        cleanup.close()


def _join_all(threads, *, timeout=15):
    for t in threads:
        t.join(timeout=timeout)
    assert all(not t.is_alive() for t in threads), "疑似死锁：线程未在超时内结束"


def test_two_workers_claim_one_order_skip_locked(pg_env):
    """A：两 worker 同时认领唯一到期订单，SKIP LOCKED → 恰一个领到。"""
    oid = f"o_claim_{pg_env['sfx']}"
    pg_env["make_paid_order"](oid)

    claimed: list = []
    barrier = threading.Barrier(2)

    def worker(name: str):
        barrier.wait()
        row = PaymentRepository().claim_next_grant(
            now=pg_env["now"] + timedelta(seconds=1), lease_owner=name
        )
        if row is not None:
            claimed.append((name, row.order_id))

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    _join_all(threads)

    assert len(claimed) == 1, f"应恰一个 worker 领到订单，实际={claimed}"
    assert claimed[0][1] == oid
    assert pg_env["grant_status_of"](oid) == "processing"


def test_concurrent_apply_grants_once(pg_env):
    """B：同订单并发原子发放 → 恰一次 granted，一次 already_granted，审计一行。"""
    oid = f"o_apply_{pg_env['sfx']}"
    pg_env["make_paid_order"](oid)  # membership 单
    svc = PaymentGrantService()

    statuses: list = []
    barrier = threading.Barrier(2)

    def worker(name: str):
        barrier.wait()
        st = PaymentRepository().apply_grant_atomic(
            order_id=oid, lease_owner=name, grant_fn=svc._grant_in_session
        )
        statuses.append(st)

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    _join_all(threads)

    assert sorted(statuses) == ["already_granted", "granted"], f"实际={statuses}"
    assert pg_env["grant_status_of"](oid) == "granted"
    assert pg_env["audit_count"](oid) == 1, "权益审计应恰一行（幂等键=order_id）"
    row = pg_env["membership_row"]()
    assert row["status"] == "active"
    assert row["version"] == 2, "会员版本从 1 递增一次（发放只发生一次）"


def test_expired_lease_reclaimed_once(pg_env):
    """C：processing 且租约过期的订单被两 worker 竞争回收 → 恰一个领到。"""
    oid = f"o_lease_{pg_env['sfx']}"
    now = pg_env["now"]
    pg_env["make_paid_order"](
        oid, grant_status="processing",
        lease_until=now - timedelta(minutes=10), lease_owner="dead_worker",
        next_retry=now,
    )

    claimed: list = []
    barrier = threading.Barrier(2)

    def worker(name: str):
        barrier.wait()
        row = PaymentRepository().claim_next_grant(now=now, lease_owner=name)
        if row is not None:
            claimed.append(name)

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    _join_all(threads)

    assert len(claimed) == 1, f"过期租约应恰一个 worker 回收，实际={claimed}"


def test_admin_and_payment_concurrent_no_lost_update(pg_env):
    """D：管理员写会员 与 支付发放会员 并发 → 支付恰发一次；管理员成功或 version_conflict。"""
    oid = f"o_race_{pg_env['sfx']}"
    pg_env["make_paid_order"](oid)  # membership 单
    now = pg_env["now"]
    svc = PaymentGrantService()

    pay_status: list = []
    admin_ok: list = []
    admin_err: list = []
    barrier = threading.Barrier(2)

    def pay_worker():
        barrier.wait()
        st = PaymentRepository().apply_grant_atomic(
            order_id=oid, lease_owner="pay", grant_fn=svc._grant_in_session
        )
        pay_status.append(st)

    def admin_worker():
        barrier.wait()
        ctx = AdminWriteContext(
            actor_user_id=pg_env["admin"],
            idempotency_key=f"race-{pg_env['sfx']}",
            request_fingerprint="race-fp",
        )
        try:
            res = AdminEntitlementService(
                enabled_markets=frozenset({"US"}), clock=lambda: datetime.now(UTC)
            ).set_membership(
                context=ctx, target_user_id=pg_env["uid"], mode="until",
                valid_until=now + timedelta(days=180), reason="并发赠送",
                expected_version=1,
            )
            admin_ok.append(res.status_code)
        except EntitlementError as e:
            admin_err.append(e.code)

    threads = [threading.Thread(target=pay_worker), threading.Thread(target=admin_worker)]
    for t in threads:
        t.start()
    _join_all(threads)

    # 支付发放恰一次（幂等键=order_id），无论交错顺序。
    assert pay_status == ["granted"], f"支付发放状态实际={pay_status}"
    assert pg_env["grant_status_of"](oid) == "granted"
    assert pg_env["audit_count"](oid) == 1, "支付权益审计应恰一行"
    # 管理员乐观锁：成功（先于支付拿锁）或 version_conflict（后于支付，陈旧写被拒）。
    assert len(admin_ok) + len(admin_err) == 1
    if admin_err:
        assert admin_err == ["entitlements.version_conflict"], f"实际={admin_err}"
    # 会员最终有效、版本单调（未被并发写覆盖损坏）。
    row = pg_env["membership_row"]()
    assert row["status"] == "active"
    assert row["version"] in (2, 3), f"版本应为 2 或 3，实际={row['version']}"
    assert row["revoked_at"] is None
