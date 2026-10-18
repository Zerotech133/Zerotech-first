"""单元 B Task B2-4：撤销会员 ↔ 开仓提交门禁的锁序线性化（PostgreSQL）。

整文件 @real_pg，需 REAL_PG=1 且真 PG（已迁移到 0030）。两场景：
- 撤权先提交 → 后续 guard.hold 读到 disabled 实例而拒 broker；
- worker 先持锁（guard.hold 持 user→membership→instance FOR UPDATE）→ 撤权阻塞在
  行锁上，直到 guard 退出（broker 返回）才提交。用锁序保证的确定性断言（非 timing）。
"""
from __future__ import annotations

import os
import threading
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from zhice_os_v2.application.services.admin_entitlement_service import (
    AdminEntitlementService,
)
from zhice_os_v2.application.services.opening_submission_guard import (
    OpeningSubmissionDenied,
    OpeningSubmissionGuard,
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


@pytest.fixture()
def pg_env():
    _skip_unless_pg()
    from sqlalchemy import text

    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session

    sfx = datetime.now(UTC).strftime("%H%M%S%f")
    uid = f"zzg_u_{sfx}"
    usid = f"zzg_usi_{sfx}"
    sid = f"zzg_s_{sfx}"
    svid = f"zzg_sv_{sfx}"
    aid = f"zzg_acc_{sfx}"
    now = datetime.now(UTC)

    s = get_session()
    try:
        # PG 强制外键：按依赖分层 flush（user/strategy → membership/version/account
        # → instance），避免 SQLAlchemy 未知字符串 FK 依赖而乱序 INSERT。
        s.add(orm.UserModel(user_id=uid, username=uid, password_hash="x",
                            role="USER", is_active=True, locale="zh", created_at=now))
        s.add(orm.StrategyModel(
            strategy_id=sid, strategy_name=sid, market="US", status="ACTIVE",
            owner_user_id=uid, visibility="PRIVATE", publication_status="PRIVATE",
            created_at=now, updated_at=now,
        ))
        s.flush()
        s.add(orm.MembershipModel(
            user_id=uid, tier="member", status="active",
            valid_until=now + timedelta(days=365), is_permanent=False, version=1,
            created_at=now, updated_at=now,
        ))
        s.add(orm.StrategyVersionModel(
            strategy_version_id=svid, strategy_id=sid, version_no="1", market="US",
            status="ACTIVE", spec={}, created_at=now,
        ))
        s.add(orm.UserAccountModel(
            account_id=aid, user_id=uid, market="US", currency="USD",
            account_type="SHARED_PORTFOLIO", initial_capital=Decimal("100000"),
            cash_balance=Decimal("100000"), reserved_cash=Decimal("0"),
            status="ACTIVE", created_at=now, updated_at=now,
        ))
        s.flush()
        s.add(orm.UserStrategyInstanceModel(
            user_strategy_id=usid, user_id=uid, strategy_version_id=svid,
            account_id=aid, enabled=True, auto_execute=False, execution_mode="MANUAL",
            max_position_ratio=Decimal("0.1"), created_at=now, updated_at=now,
        ))
        s.commit()
    finally:
        s.close()

    ids = {"uid": uid, "usid": usid, "sid": sid, "svid": svid, "aid": aid}
    yield ids

    cleanup = get_session()
    try:
        cleanup.execute(text("DELETE FROM v2_entitlement_audit WHERE target_user_id=:u"), {"u": uid})
        cleanup.execute(text("DELETE FROM v2_admin_operation WHERE target_user_id=:u"), {"u": uid})
        cleanup.execute(text("DELETE FROM v2_account_reservation WHERE user_strategy_id=:i"), {"i": usid})
        cleanup.execute(text("DELETE FROM v2_user_strategy_instance WHERE user_strategy_id=:i"), {"i": usid})
        cleanup.execute(text("DELETE FROM v2_user_account WHERE account_id=:a"), {"a": aid})
        cleanup.execute(text("DELETE FROM v2_strategy_version WHERE strategy_version_id=:v"), {"v": svid})
        cleanup.execute(text("DELETE FROM v2_strategy WHERE strategy_id=:s"), {"s": sid})
        cleanup.execute(text("DELETE FROM v2_membership WHERE user_id=:u"), {"u": uid})
        cleanup.execute(text("DELETE FROM v2_user WHERE user_id=:u"), {"u": uid})
        cleanup.commit()
    finally:
        cleanup.close()


def _revoke_svc() -> AdminEntitlementService:
    return AdminEntitlementService(clock=lambda: datetime.now(UTC))


def test_revoke_first_then_guard_denies(pg_env):
    """撤权先提交（停用实例）→ 后续 guard.hold 读到 disabled 而拒。"""
    uid, usid = pg_env["uid"], pg_env["usid"]
    r = _revoke_svc().revoke_membership(
        context=AdminWriteContext(actor_user_id=uid, idempotency_key="rev-first-001",
                                  request_fingerprint="fp-rev-first"),
        target_user_id=uid, reason="撤销", expected_version=1,
    )
    assert usid in r.body["opening_risk_cleanup"]["disabled_instance_ids"]
    with pytest.raises(OpeningSubmissionDenied) as e, \
            OpeningSubmissionGuard().hold(user_strategy_id=usid):
        pass
    assert e.value.code in ("opening.instance_disabled", "opening.membership_required")


def test_guard_holds_then_revoke_waits(pg_env):
    """worker 先持锁 → 撤权阻塞到 guard 退出（broker 返回）后才提交。

    确定性断言（非 timing）：撤权完成时刻晚于 guard 退出时刻——因撤权须先拿 user 行锁，
    而该锁被 guard.hold 持有到退出。附加：无死锁、撤权后实例被停用。
    """
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session

    uid, usid = pg_env["uid"], pg_env["usid"]
    holding = threading.Event()
    marks: dict[str, float] = {}
    errors: list[str] = []

    def worker_hold():
        try:
            with OpeningSubmissionGuard().hold(user_strategy_id=usid):
                holding.set()
                time.sleep(1.0)  # 模拟 broker 提交耗时，持锁窗口
                marks["hold_exit"] = time.monotonic()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"hold:{exc!r}")

    def worker_revoke():
        try:
            holding.wait(timeout=5)
            _revoke_svc().revoke_membership(
                context=AdminWriteContext(
                    actor_user_id=uid, idempotency_key="rev-wait-001",
                    request_fingerprint="fp-rev-wait"),
                target_user_id=uid, reason="撤销", expected_version=1,
            )
            marks["revoke_done"] = time.monotonic()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"revoke:{exc!r}")

    th = threading.Thread(target=worker_hold)
    tr = threading.Thread(target=worker_revoke)
    th.start()
    tr.start()
    th.join(timeout=15)
    tr.join(timeout=15)

    assert not th.is_alive() and not tr.is_alive(), "疑似死锁"
    assert errors == [], f"线程异常：{errors}"
    assert "hold_exit" in marks and "revoke_done" in marks
    # 撤权被 guard 持锁窗口线性化到 broker 返回之后。
    assert marks["revoke_done"] >= marks["hold_exit"]

    check = get_session()
    try:
        inst = check.query(orm.UserStrategyInstanceModel).filter_by(
            user_strategy_id=usid).one()
        assert inst.enabled is False
    finally:
        check.close()
