"""单元 B Task B1-10：管理员权益写并发验证（PostgreSQL）。

整文件 @real_pg，需 REAL_PG=1 且真 PG（已迁移到 0030）。覆盖：
- 同 key 同指纹 → 一结果一 operation（另一方幂等复放）；
- 同 key 异指纹 → 一成功一 idempotency_conflict；
- 同资源异 key 同版本 → 一成功一 version_conflict（user 行锁串行化）；
- 批量任一失败 → 整批回滚（无 operation/audit/订阅残留）；
- 两管理员并发互相降级 → 不死锁、恰一成功一 last_admin_protected、保一 active ADMIN。
只识别 uq_v2_admin_operation_actor_key；其他完整性错误应上抛。
"""
from __future__ import annotations

import os
import threading
from datetime import UTC, datetime, timedelta

import pytest

from zhice_os_v2.application.services.admin_entitlement_service import (
    AdminEntitlementService,
    EntitlementError,
)
from zhice_os_v2.domain.billing.admin_entitlement import AdminWriteContext

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
        pytest.skip("需要 REAL_PG=1 且真 PostgreSQL 可用（已迁移到 0030）")


def _svc() -> AdminEntitlementService:
    return AdminEntitlementService(
        enabled_markets=frozenset({"US"}), clock=lambda: datetime.now(UTC)
    )


def _ctx(key: str, fp: str) -> AdminWriteContext:
    return AdminWriteContext(actor_user_id="zzc_actor", idempotency_key=key,
                             request_fingerprint=fp)


@pytest.fixture()
def pg_env():
    _skip_unless_pg()
    from sqlalchemy import text

    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session

    sfx = datetime.now(UTC).strftime("%H%M%S%f")
    uid = f"zzc_u_{sfx}"
    a1, a2 = f"zzc_a1_{sfx}", f"zzc_a2_{sfx}"
    sid_a, sid_b, sid_bad = f"zzc_sa_{sfx}", f"zzc_sb_{sfx}", f"zzc_sbad_{sfx}"
    now = datetime.now(UTC)

    s = get_session()
    try:
        # actor 仅作 operation 归属（非 ADMIN，避免污染最后管理员计数）。
        s.add(orm.UserModel(user_id="zzc_actor", username=f"zzc_actor_{sfx}",
                            password_hash="x", role="USER", is_active=True,
                            locale="zh", created_at=now))
        s.add(orm.UserModel(user_id=uid, username=f"zzc_t_{sfx}", password_hash="x",
                            role="USER", is_active=True, locale="zh", created_at=now))
        # a1/a2 是仅有的两个 ADMIN → 互相降级触发最后管理员竞态。
        for aid in (a1, a2):
            s.add(orm.UserModel(user_id=aid, username=aid, password_hash="x",
                                role="ADMIN", is_active=True, locale="zh",
                                created_at=now))
        for stid, pub, vis, owner in (
            (sid_a, "PUBLISHED", "PUBLIC", None),
            (sid_b, "PUBLISHED", "PUBLIC", None),
            (sid_bad, "PRIVATE", "PRIVATE", "someone"),
        ):
            s.add(orm.StrategyModel(
                strategy_id=stid, strategy_name=stid, market="US", status="ACTIVE",
                owner_user_id=owner, visibility=vis, publication_status=pub,
                created_at=now, updated_at=now,
            ))
        s.commit()
    finally:
        s.close()

    ids = {"uid": uid, "a1": a1, "a2": a2, "sid_a": sid_a, "sid_b": sid_b,
           "sid_bad": sid_bad}
    yield ids

    cleanup = get_session()
    try:
        allu = [uid, a1, a2, "zzc_actor"]
        alls = [sid_a, sid_b, sid_bad]
        cleanup.execute(text("DELETE FROM v2_entitlement_audit WHERE actor_user_id='zzc_actor' OR target_user_id = ANY(:u)"), {"u": allu})
        cleanup.execute(text("DELETE FROM v2_admin_operation WHERE actor_user_id='zzc_actor'"))
        cleanup.execute(text("DELETE FROM v2_strategy_subscription WHERE user_id = ANY(:u)"), {"u": allu})
        cleanup.execute(text("DELETE FROM v2_membership WHERE user_id = ANY(:u)"), {"u": allu})
        cleanup.execute(text("DELETE FROM v2_strategy WHERE strategy_id = ANY(:s)"), {"s": alls})
        cleanup.execute(text("DELETE FROM v2_user WHERE user_id = ANY(:u)"), {"u": allu})
        cleanup.commit()
    finally:
        cleanup.close()


def _count_ops(key: str) -> int:
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session
    s = get_session()
    try:
        return s.query(orm.AdminOperationModel).filter_by(
            actor_user_id="zzc_actor", idempotency_key=key).count()
    finally:
        s.close()


def test_same_key_same_fingerprint_one_operation(pg_env):
    uid = pg_env["uid"]
    ctx = _ctx("cc-samekey-01", "fp-same")
    results: list = []
    errors: list = []
    barrier = threading.Barrier(2)

    def worker():
        barrier.wait()
        try:
            r = _svc().set_membership(context=ctx, target_user_id=uid,
                                      mode="permanent", valid_until=None,
                                      reason="并发", expected_version=0)
            results.append(r)
        except EntitlementError as e:
            errors.append(e.code)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert all(not t.is_alive() for t in threads), "疑似死锁"
    assert errors == [], f"同指纹不应冲突：{errors}"
    assert len(results) == 2
    assert results[0].body == results[1].body
    assert _count_ops("cc-samekey-01") == 1


def test_same_key_diff_fingerprint_one_conflict(pg_env):
    uid = pg_env["uid"]
    results: list = []
    errors: list = []
    barrier = threading.Barrier(2)

    def worker(fp: str):
        barrier.wait()
        try:
            r = _svc().set_membership(
                context=_ctx("cc-diffp-01", fp), target_user_id=uid,
                mode="permanent", valid_until=None, reason="并发",
                expected_version=0)
            results.append(r)
        except EntitlementError as e:
            errors.append(e.code)

    threads = [threading.Thread(target=worker, args=(fp,))
               for fp in ("fpA", "fpB")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert all(not t.is_alive() for t in threads), "疑似死锁"
    assert len(results) == 1, f"应恰一成功：results={len(results)} errors={errors}"
    assert errors == ["entitlements.idempotency_conflict"]
    assert _count_ops("cc-diffp-01") == 1


def test_diff_key_same_version_one_version_conflict(pg_env):
    uid = pg_env["uid"]
    future = datetime.now(UTC) + timedelta(days=365)
    results: list = []
    errors: list = []
    barrier = threading.Barrier(2)

    def worker(key: str):
        barrier.wait()
        try:
            r = _svc().set_membership(
                context=_ctx(key, "fp-" + key), target_user_id=uid,
                mode="until", valid_until=future, reason="并发",
                expected_version=0)
            results.append(r)
        except EntitlementError as e:
            errors.append(e.code)

    threads = [threading.Thread(target=worker, args=(k,))
               for k in ("cc-vk-A0001", "cc-vk-B0001")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert all(not t.is_alive() for t in threads), "疑似死锁"
    assert len(results) == 1, f"应恰一成功：results={len(results)} errors={errors}"
    assert errors == ["entitlements.version_conflict"]


def test_batch_failure_rolls_back_entirely(pg_env):
    uid, sid_a, sid_bad = pg_env["uid"], pg_env["sid_a"], pg_env["sid_bad"]
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session

    with pytest.raises(EntitlementError) as e:
        _svc().set_subscriptions(
            context=_ctx("cc-batch-01", "fp-batch"), target_user_id=uid,
            strategy_ids=[sid_a, sid_bad], mode="permanent", valid_until=None,
            reason="批量", expected_versions={sid_a: 0, sid_bad: 0})
    assert e.value.code == "entitlements.strategy_not_grantable"
    s = get_session()
    try:
        assert s.query(orm.StrategySubscriptionModel).filter_by(
            user_id=uid).count() == 0
        assert _count_ops("cc-batch-01") == 0
    finally:
        s.close()


def test_two_admins_demote_no_deadlock_keeps_one(pg_env):
    a1, a2 = pg_env["a1"], pg_env["a2"]
    from sqlalchemy import text

    from zhice_os_v2.infrastructure.db.session import get_session

    successes: list = []
    failures: list = []
    barrier = threading.Barrier(2)

    def worker(target: str, key: str):
        barrier.wait()
        try:
            _svc().change_role(context=_ctx(key, "fp-" + key), target_user_id=target,
                               new_role="USER", reason="并发", expected_auth_version=1)
            successes.append(target)
        except EntitlementError as e:
            failures.append(e.code)

    threads = [
        threading.Thread(target=worker, args=(a1, "cc-role-A001")),
        threading.Thread(target=worker, args=(a2, "cc-role-B001")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert all(not t.is_alive() for t in threads), "疑似死锁"
    assert len(successes) == 1, f"应恰一成功：{successes} / {failures}"
    assert failures == ["auth.last_admin_protected"], f"failures={failures}"
    check = get_session()
    try:
        n = check.execute(
            text("SELECT count(*) FROM v2_user WHERE role='ADMIN' "
                 "AND is_active=true AND user_id = ANY(:a)"),
            {"a": [a1, a2]},
        ).scalar_one()
        assert n == 1, f"必须保留恰一个 active ADMIN，实为 {n}"
    finally:
        check.close()
