"""账户原子资金预留（Phase B Task 3）测试。

两阶段资金语义：
- reserve  冻结：reserved_cash += amount（cash_balance 不动），available = cash - reserved。
- consume  实扣：reserved_cash -= amount 且 cash_balance -= amount（成交落款）。
- release  解冻：reserved_cash -= amount（拒绝/取消/错误回滚）。

隔离测试跑内存 sqlite（monkeypatch 三处 get_session）验证逻辑正确性与幂等；真正的并发
（两 session 争抢同一账户可用现金）需 @pytest.mark.real_pg 直连真 PG 的 SELECT ... FOR
UPDATE 行锁串行化——SQLite 下 with_for_update 为 no-op，无法在进程内复现，故并发场景单列。
"""
from __future__ import annotations

import os
from decimal import Decimal
from types import SimpleNamespace

import pytest

from zhice_os_v2.domain.accounts.reservation import ReservationStatus


# ──────────────────────────────────────────────────────────────────────────────
# 隔离内存后端 fixtures
# ──────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def ledger_env(monkeypatch, users):
    """共享一个隔离 sqlite：账户仓储 / 实例仓储 / 台账服务都指向它。

    策略/版本注册进 InMemoryDatabase（catalog），实例经 UserStrategyService 建。
    返回 ledger / account_service / instance_service / users。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import zhice_os_v2.application.services.account_ledger_service as ledger_mod
    import zhice_os_v2.infrastructure.repositories.user_account_repository as acc_repo_mod
    import zhice_os_v2.infrastructure.repositories.user_strategy_repository as inst_repo_mod
    from zhice_os_v2.application.services.account_ledger_service import (
        AccountLedgerService,
    )
    from zhice_os_v2.application.services.strategy_catalog_service import (
        StrategyCatalogService,
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
    for mod in (ledger_mod, acc_repo_mod, inst_repo_mod):
        monkeypatch.setattr(mod, "get_session", lambda: Session())

    db = InMemoryDatabase()
    account_service = UserAccountService()
    instance_service = UserStrategyService(db=db, account_service=account_service)

    catalog = StrategyCatalogService(db)
    strat = catalog.create_public("resv-strategy", market="US")
    version = next(
        v for v in db.list_strategy_versions() if v.strategy_id == strat.strategy_id
    )

    return SimpleNamespace(
        ledger=AccountLedgerService(),
        account_service=account_service,
        instance_service=instance_service,
        version=version,
        users=users,
    )


@pytest.fixture
def shared_account(ledger_env):
    return ledger_env.account_service.create(
        ledger_env.users.alice,
        market="US",
        account_type="SHARED_PORTFOLIO",
        initial_capital=100000,
    )


def _make_instance(ledger_env, shared_account, quota):
    return ledger_env.instance_service.create(
        ledger_env.users.alice,
        strategy_version_id=ledger_env.version.strategy_version_id,
        account_id=shared_account.account_id,
        capital_quota=quota,
    )


@pytest.fixture
def instance(ledger_env, shared_account):
    """不限额实例（capital_quota=None），仅受账户可用现金约束。"""
    return _make_instance(ledger_env, shared_account, None)


# ──────────────────────────────────────────────────────────────────────────────
# reserve
# ──────────────────────────────────────────────────────────────────────────────
def test_reserve_reduces_available_cash(ledger_env, shared_account, instance):
    ledger = ledger_env.ledger
    reservation = ledger.reserve(
        shared_account.account_id,
        instance.user_strategy_id,
        Decimal("30000"),
        "plan-1",
    )
    account = ledger.get(shared_account.account_id)
    assert reservation.amount == Decimal("30000")
    assert reservation.status == ReservationStatus.RESERVED
    # 冻结：cash_balance 不动，reserved_cash 升；available = 100k - 30k = 70k
    assert account.cash_balance == Decimal("100000")
    assert account.reserved_cash == Decimal("30000")
    assert account.cash_balance - account.reserved_cash == Decimal("70000")


def test_reserve_rejected_when_insufficient_cash(ledger_env, shared_account, instance):
    ledger = ledger_env.ledger
    ledger.reserve(shared_account.account_id, instance.user_strategy_id, Decimal("80000"), "p1")
    with pytest.raises(ValueError, match="account.insufficient_cash"):
        ledger.reserve(
            shared_account.account_id, instance.user_strategy_id, Decimal("30000"), "p2"
        )
    # 失败不改账户
    account = ledger.get(shared_account.account_id)
    assert account.reserved_cash == Decimal("80000")


def test_reserve_rejects_non_positive_amount(ledger_env, shared_account, instance):
    ledger = ledger_env.ledger
    with pytest.raises(ValueError):
        ledger.reserve(
            shared_account.account_id, instance.user_strategy_id, Decimal("0"), "p0"
        )


def test_reserve_rejects_inactive_account(ledger_env, shared_account, instance):
    ledger = ledger_env.ledger
    # 冻结账户（直连共享 sqlite 改状态）
    import zhice_os_v2.infrastructure.repositories.user_account_repository as acc_repo_mod
    from zhice_os_v2.infrastructure.db import models as orm

    s = acc_repo_mod.get_session()
    try:
        row = (
            s.query(orm.UserAccountModel)
            .filter_by(account_id=shared_account.account_id)
            .first()
        )
        row.status = "FROZEN"
        s.commit()
    finally:
        s.close()
    with pytest.raises(ValueError, match="account.not_active"):
        ledger.reserve(
            shared_account.account_id, instance.user_strategy_id, Decimal("100"), "pf"
        )


# ──────────────────────────────────────────────────────────────────────────────
# 策略配额
# ──────────────────────────────────────────────────────────────────────────────
def test_strategy_quota_is_enforced(ledger_env, shared_account):
    ledger = ledger_env.ledger
    instance = _make_instance(ledger_env, shared_account, Decimal("20000"))
    with pytest.raises(ValueError, match="account.strategy_quota_exceeded"):
        ledger.reserve(
            shared_account.account_id, instance.user_strategy_id, Decimal("20001"), "q1"
        )


def test_strategy_quota_sums_active_reservations(ledger_env, shared_account):
    ledger = ledger_env.ledger
    instance = _make_instance(ledger_env, shared_account, Decimal("20000"))
    ledger.reserve(shared_account.account_id, instance.user_strategy_id, Decimal("15000"), "q1")
    # 已占 15k，再 6k → 21k > 20k 配额
    with pytest.raises(ValueError, match="account.strategy_quota_exceeded"):
        ledger.reserve(
            shared_account.account_id, instance.user_strategy_id, Decimal("6000"), "q2"
        )
    # 恰好补满 5k 通过
    r = ledger.reserve(
        shared_account.account_id, instance.user_strategy_id, Decimal("5000"), "q3"
    )
    assert r.status == ReservationStatus.RESERVED


def test_none_quota_only_bound_by_cash(ledger_env, shared_account, instance):
    ledger = ledger_env.ledger
    # capital_quota=None：可预留至账户可用现金上限
    r = ledger.reserve(
        shared_account.account_id, instance.user_strategy_id, Decimal("100000"), "full"
    )
    assert r.amount == Decimal("100000")


# ──────────────────────────────────────────────────────────────────────────────
# 幂等
# ──────────────────────────────────────────────────────────────────────────────
def test_reserve_idempotent_key_returns_existing_no_double_charge(
    ledger_env, shared_account, instance
):
    ledger = ledger_env.ledger
    r1 = ledger.reserve(shared_account.account_id, instance.user_strategy_id, Decimal("30000"), "same-key")
    r2 = ledger.reserve(shared_account.account_id, instance.user_strategy_id, Decimal("30000"), "same-key")
    assert r1.reservation_id == r2.reservation_id
    account = ledger.get(shared_account.account_id)
    # 不双扣
    assert account.reserved_cash == Decimal("30000")


# ──────────────────────────────────────────────────────────────────────────────
# consume / release
# ──────────────────────────────────────────────────────────────────────────────
def test_consume_reduces_both_balances(ledger_env, shared_account, instance):
    ledger = ledger_env.ledger
    r = ledger.reserve(shared_account.account_id, instance.user_strategy_id, Decimal("30000"), "c1")
    consumed = ledger.consume(r.reservation_id)
    assert consumed.status == ReservationStatus.CONSUMED
    account = ledger.get(shared_account.account_id)
    # 实扣：reserved 归零、cash 降 30k
    assert account.reserved_cash == Decimal("0")
    assert account.cash_balance == Decimal("70000")


def test_release_only_reduces_reserved(ledger_env, shared_account, instance):
    ledger = ledger_env.ledger
    r = ledger.reserve(shared_account.account_id, instance.user_strategy_id, Decimal("30000"), "r1")
    released = ledger.release(r.reservation_id)
    assert released.status == ReservationStatus.RELEASED
    account = ledger.get(shared_account.account_id)
    # 解冻：reserved 归零、cash 不动
    assert account.reserved_cash == Decimal("0")
    assert account.cash_balance == Decimal("100000")


def test_consume_is_idempotent(ledger_env, shared_account, instance):
    ledger = ledger_env.ledger
    r = ledger.reserve(shared_account.account_id, instance.user_strategy_id, Decimal("30000"), "c1")
    ledger.consume(r.reservation_id)
    ledger.consume(r.reservation_id)  # 二次不重复扣
    account = ledger.get(shared_account.account_id)
    assert account.reserved_cash == Decimal("0")
    assert account.cash_balance == Decimal("70000")


def test_release_is_idempotent(ledger_env, shared_account, instance):
    ledger = ledger_env.ledger
    r = ledger.reserve(shared_account.account_id, instance.user_strategy_id, Decimal("30000"), "r1")
    ledger.release(r.reservation_id)
    ledger.release(r.reservation_id)  # 二次不重复解冻
    account = ledger.get(shared_account.account_id)
    assert account.reserved_cash == Decimal("0")
    assert account.cash_balance == Decimal("100000")


def test_release_after_consume_rejected(ledger_env, shared_account, instance):
    ledger = ledger_env.ledger
    r = ledger.reserve(shared_account.account_id, instance.user_strategy_id, Decimal("30000"), "c1")
    ledger.consume(r.reservation_id)
    with pytest.raises(ValueError):
        ledger.release(r.reservation_id)


def test_consume_after_release_rejected(ledger_env, shared_account, instance):
    ledger = ledger_env.ledger
    r = ledger.reserve(shared_account.account_id, instance.user_strategy_id, Decimal("30000"), "r1")
    ledger.release(r.reservation_id)
    with pytest.raises(ValueError):
        ledger.consume(r.reservation_id)


# ──────────────────────────────────────────────────────────────────────────────
# money-safety 守卫（复核必修）
# ──────────────────────────────────────────────────────────────────────────────
def test_idempotency_key_reused_with_different_amount_rejected(
    ledger_env, shared_account, instance
):
    """[#1] 同 key 复用却传不同额：拒绝，不静默返回错误金额预留。"""
    ledger = ledger_env.ledger
    ledger.reserve(shared_account.account_id, instance.user_strategy_id, Decimal("30000"), "dup")
    with pytest.raises(ValueError, match="account.idempotency_amount_mismatch"):
        ledger.reserve(
            shared_account.account_id, instance.user_strategy_id, Decimal("20000"), "dup"
        )
    # 原预留不变
    account = ledger.get(shared_account.account_id)
    assert account.reserved_cash == Decimal("30000")


def test_instance_must_belong_to_account(ledger_env, shared_account, instance):
    """[#2] user_strategy_id 必须属于 account_id，否则拒绝（防归属/双账户漏洞）。"""
    ledger = ledger_env.ledger
    # 另建一个账户；instance 属于 shared_account，与 other 账户不符
    other = ledger_env.account_service.create(
        ledger_env.users.alice,
        market="US",
        account_type="SHARED_PORTFOLIO",
        initial_capital=100000,
    )
    with pytest.raises(ValueError, match="account.instance_account_mismatch"):
        ledger.reserve(
            other.account_id, instance.user_strategy_id, Decimal("1000"), "mm"
        )


def test_reserve_rejects_float_amount(ledger_env, shared_account, instance):
    """[#6] amount 拒绝 float（money 精度边界）。"""
    ledger = ledger_env.ledger
    with pytest.raises(ValueError):
        ledger.reserve(
            shared_account.account_id, instance.user_strategy_id, 30000.0, "f1"
        )


def test_reserve_rejects_unknown_account(ledger_env, shared_account, instance):
    ledger = ledger_env.ledger
    with pytest.raises(ValueError, match="account.not_found"):
        ledger.reserve(
            "acct_does_not_exist", instance.user_strategy_id, Decimal("100"), "na"
        )


def test_reserve_rejects_unknown_instance(ledger_env, shared_account):
    ledger = ledger_env.ledger
    with pytest.raises(ValueError, match="account.instance_not_found"):
        ledger.reserve(
            shared_account.account_id, "usid_does_not_exist", Decimal("100"), "ni"
        )


def test_consume_unknown_reservation_rejected(ledger_env):
    ledger = ledger_env.ledger
    with pytest.raises(ValueError, match="account.reservation_not_found"):
        ledger.consume("resv_does_not_exist")


def test_release_unknown_reservation_rejected(ledger_env):
    ledger = ledger_env.ledger
    with pytest.raises(ValueError, match="account.reservation_not_found"):
        ledger.release("resv_does_not_exist")


# ──────────────────────────────────────────────────────────────────────────────
# real_pg 并发：两 session 各 reserve 70k from 100k，恰好一个成功
# ──────────────────────────────────────────────────────────────────────────────
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


@pytest.mark.real_pg
def test_concurrent_reserve_exactly_one_succeeds():
    """两并发线程各 reserve 70k from 100k：恰好一个成功，reserved_cash 最终=70k 不超 100k。

    真 PG 的 SELECT ... FOR UPDATE 行锁串行化两事务：先者锁账户→available 100k→预留 70k
    →提交；后者阻塞至先者提交后重读→available 30k<70k→account.insufficient_cash。
    """
    if not (os.getenv("REAL_PG") == "1" and _pg_available()):
        pytest.skip("需要 REAL_PG=1 且真 PostgreSQL 可用")

    import threading
    from datetime import UTC, datetime

    from zhice_os_v2.application.services.account_ledger_service import (
        AccountLedgerService,
    )
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session

    sfx = datetime.now(UTC).strftime("%H%M%S%f")
    uid = f"zztest_resv_user_{sfx}"
    sid = f"zztest_resv_strat_{sfx}"
    vid = f"zztest_resv_ver_{sfx}"
    aid = f"zztest_resv_acct_{sfx}"
    usid = f"zztest_resv_inst_{sfx}"
    now = datetime.now(UTC)

    setup = get_session()
    try:
        # 按 FK 依赖顺序逐条 flush，避免单事务内插入次序违反外键。
        setup.add(
            orm.UserModel(
                user_id=uid, username=uid, password_hash="x", role="TRADER",
                is_active=True, locale="zh", created_at=now,
            )
        )
        setup.flush()
        setup.add(
            orm.StrategyModel(
                strategy_id=sid, strategy_name="zztest", market="US",
                status="ACTIVE", visibility="PUBLIC", publication_status="PUBLISHED",
                created_at=now, updated_at=now,
            )
        )
        setup.flush()
        setup.add(
            orm.StrategyVersionModel(
                strategy_version_id=vid, strategy_id=sid, version_no="1",
                market="US", status="ACTIVE", spec={}, created_at=now,
            )
        )
        setup.flush()
        setup.add(
            orm.UserAccountModel(
                account_id=aid, user_id=uid, market="US", currency="USD",
                account_type="SHARED_PORTFOLIO",
                initial_capital=Decimal("100000"),
                cash_balance=Decimal("100000"), reserved_cash=Decimal("0"),
                status="ACTIVE", created_at=now, updated_at=now,
            )
        )
        setup.flush()
        setup.add(
            orm.UserStrategyInstanceModel(
                user_strategy_id=usid, user_id=uid, strategy_version_id=vid,
                account_id=aid, enabled=True, auto_execute=False,
                execution_mode="MANUAL", capital_quota=None,
                max_position_ratio=Decimal("0.1"),
                created_at=now, updated_at=now,
            )
        )
        setup.commit()
    finally:
        setup.close()

    ledger = AccountLedgerService()
    results: list = []
    errors: list = []
    barrier = threading.Barrier(2)

    def worker(key: str):
        barrier.wait()
        try:
            results.append(ledger.reserve(aid, usid, Decimal("70000"), key))
        except ValueError as exc:
            errors.append(str(exc))

    try:
        threads = [
            threading.Thread(target=worker, args=(f"k1_{sfx}",)),
            threading.Thread(target=worker, args=(f"k2_{sfx}",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 1, f"应恰好一个成功，实际 {len(results)}；errors={errors}"
        assert len(errors) == 1
        assert "account.insufficient_cash" in errors[0]

        final = ledger.get(aid)
        assert final.reserved_cash == Decimal("70000")
        assert final.reserved_cash <= final.cash_balance
    finally:
        cleanup = get_session()
        try:
            from sqlalchemy import delete

            cleanup.execute(
                delete(orm.AccountReservationModel).where(
                    orm.AccountReservationModel.account_id == aid
                )
            )
            cleanup.execute(
                delete(orm.UserStrategyInstanceModel).where(
                    orm.UserStrategyInstanceModel.user_strategy_id == usid
                )
            )
            cleanup.execute(
                delete(orm.UserAccountModel).where(orm.UserAccountModel.account_id == aid)
            )
            cleanup.execute(
                delete(orm.StrategyVersionModel).where(
                    orm.StrategyVersionModel.strategy_version_id == vid
                )
            )
            cleanup.execute(
                delete(orm.StrategyModel).where(orm.StrategyModel.strategy_id == sid)
            )
            cleanup.execute(
                delete(orm.UserModel).where(orm.UserModel.user_id == uid)
            )
            cleanup.commit()
        finally:
            cleanup.close()
