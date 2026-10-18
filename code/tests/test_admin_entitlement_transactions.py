"""单元 B Task B1-6：管理员权益原子事务（幂等 + 乐观锁 + 批量 + 逐资源审计）。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.application.services.admin_entitlement_service import (
    AdminEntitlementService,
    EntitlementError,
)
from zhice_os_v2.domain.billing.admin_entitlement import AdminWriteContext
from zhice_os_v2.domain.strategy.strategy import StrategyStatus
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.base import Base

NOW = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
FUTURE = NOW + timedelta(days=365)


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
    s.add(orm.UserModel(user_id="u-t", username="t", password_hash="x", role="USER",
                        is_active=True, created_at=NOW))
    s.add(orm.UserModel(user_id="u-inactive", username="inact", password_hash="x",
                        role="USER", is_active=False, created_at=NOW))
    for sid in ("strat-a", "strat-b"):
        s.add(orm.StrategyModel(
            strategy_id=sid, strategy_name=sid, market="US",
            status=StrategyStatus.ACTIVE.value, owner_user_id=None,
            visibility="PUBLIC", publication_status="PUBLISHED",
            created_at=NOW, updated_at=NOW,
        ))
    s.commit()
    s.close()
    svc = AdminEntitlementService(
        session_factory=lambda: Session(),
        enabled_markets=frozenset({"US", "CRYPTO"}),
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


# ── 会员 ─────────────────────────────────────────────────────────────

def test_set_membership_until_creates_version_one(env):
    Session, svc = env
    r = svc.set_membership(context=_ctx(), target_user_id="u-t", mode="until",
                           valid_until=FUTURE, reason="年费", expected_version=0)
    assert r.status_code == 200
    assert r.body["membership"]["effective_status"] == "active"
    assert r.body["membership"]["version"] == 1
    assert _counts(Session) == (1, 1)


def test_set_membership_permanent(env):
    Session, svc = env
    r = svc.set_membership(context=_ctx(), target_user_id="u-t", mode="permanent",
                           valid_until=None, reason="永久", expected_version=0)
    assert r.body["membership"]["effective_status"] == "permanent"
    assert r.body["membership"]["is_permanent"] is True


def test_set_membership_stale_version_conflict(env):
    Session, svc = env
    svc.set_membership(context=_ctx(key="k-0000001a"), target_user_id="u-t",
                       mode="until", valid_until=FUTURE, reason="x", expected_version=0)
    with pytest.raises(EntitlementError, match="version_conflict"):
        svc.set_membership(context=_ctx(key="k-0000001b"), target_user_id="u-t",
                           mode="until", valid_until=FUTURE, reason="x",
                           expected_version=0)


def test_set_membership_inactive_rejected(env):
    _, svc = env
    with pytest.raises(EntitlementError, match="user_inactive"):
        svc.set_membership(context=_ctx(), target_user_id="u-inactive", mode="permanent",
                           valid_until=None, reason="x", expected_version=0)


def test_set_membership_past_date_rejected(env):
    _, svc = env
    with pytest.raises(EntitlementError, match="invalid_expiry"):
        svc.set_membership(context=_ctx(), target_user_id="u-t", mode="until",
                           valid_until=NOW - timedelta(days=1), reason="x",
                           expected_version=0)


def test_revoke_missing_membership(env):
    _, svc = env
    with pytest.raises(EntitlementError, match="membership_not_granted"):
        svc.revoke_membership_core(context=_ctx(), target_user_id="u-t",
                                   reason="x", expected_version=0)


def test_revoke_membership_then_noop(env):
    Session, svc = env
    svc.set_membership(context=_ctx(key="k-set-0001"), target_user_id="u-t",
                       mode="permanent", valid_until=None, reason="x",
                       expected_version=0)
    r = svc.revoke_membership_core(context=_ctx(key="k-rev-0001"),
                                   target_user_id="u-t", reason="退款", expected_version=1)
    assert r.body["membership"]["effective_status"] == "revoked"
    # 再撤销（版本已 2）→ no-op 不递增
    r2 = svc.revoke_membership_core(context=_ctx(key="k-rev-0002"),
                                    target_user_id="u-t", reason="再撤", expected_version=2)
    assert r2.body.get("noop") is True


# ── 幂等 ─────────────────────────────────────────────────────────────

def test_same_key_same_fingerprint_replays(env):
    Session, svc = env
    first = svc.set_membership(context=_ctx(key="k-idem-001", fp="same"),
                               target_user_id="u-t", mode="permanent", valid_until=None,
                               reason="x", expected_version=0)
    replay = svc.set_membership(context=_ctx(key="k-idem-001", fp="same"),
                                target_user_id="u-t", mode="permanent", valid_until=None,
                                reason="x", expected_version=0)
    assert replay.replayed is True
    assert replay.body == first.body
    assert _counts(Session)[0] == 1  # 仅一条 operation


def test_same_key_different_fingerprint_conflicts(env):
    _, svc = env
    svc.set_membership(context=_ctx(key="k-idem-002", fp="fp-a"), target_user_id="u-t",
                       mode="permanent", valid_until=None, reason="x", expected_version=0)
    with pytest.raises(EntitlementError, match="idempotency_conflict"):
        svc.set_membership(context=_ctx(key="k-idem-002", fp="fp-b"), target_user_id="u-t",
                           mode="permanent", valid_until=None, reason="x",
                           expected_version=0)


# ── 批量订阅 ─────────────────────────────────────────────────────────

def test_batch_set_one_operation_per_strategy_audit(env):
    Session, svc = env
    r = svc.set_subscriptions(context=_ctx(), target_user_id="u-t",
                              strategy_ids=["strat-b", "strat-a", "strat-a"],
                              mode="permanent", valid_until=None, reason="套餐",
                              expected_versions={"strat-a": 0, "strat-b": 0})
    ids = [x["strategy_id"] for x in r.body["subscriptions"]]
    assert ids == ["strat-a", "strat-b"]  # 去重 + 排序
    assert _counts(Session) == (1, 2)  # 一 operation + 每策略一 audit


def test_batch_versions_mismatch(env):
    _, svc = env
    with pytest.raises(EntitlementError, match="versions_mismatch"):
        svc.set_subscriptions(context=_ctx(), target_user_id="u-t",
                              strategy_ids=["strat-a", "strat-b"], mode="permanent",
                              valid_until=None, reason="x",
                              expected_versions={"strat-a": 0})


def test_batch_one_stale_rolls_back_all(env):
    Session, svc = env
    with pytest.raises(EntitlementError, match="version_conflict"):
        svc.set_subscriptions(context=_ctx(), target_user_id="u-t",
                              strategy_ids=["strat-a", "strat-b"], mode="permanent",
                              valid_until=None, reason="x",
                              expected_versions={"strat-a": 0, "strat-b": 7})
    # 整批回滚：strat-a 也未写入
    s = Session()
    try:
        assert s.query(orm.StrategySubscriptionModel).count() == 0
    finally:
        s.close()
    assert _counts(Session) == (0, 0)


def test_batch_non_grantable_strategy_rejected(env):
    Session, svc = env
    s = Session()
    s.add(orm.StrategyModel(strategy_id="strat-priv", strategy_name="p", market="US",
                            status=StrategyStatus.ACTIVE.value, owner_user_id="someone",
                            visibility="PRIVATE", publication_status="PRIVATE",
                            created_at=NOW, updated_at=NOW))
    s.commit()
    s.close()
    with pytest.raises(EntitlementError, match="strategy_not_grantable"):
        svc.set_subscriptions(context=_ctx(), target_user_id="u-t",
                              strategy_ids=["strat-priv"], mode="permanent",
                              valid_until=None, reason="x",
                              expected_versions={"strat-priv": 0})


def test_revoke_subscription_missing(env):
    _, svc = env
    with pytest.raises(EntitlementError, match="subscription_not_granted"):
        svc.revoke_subscriptions(context=_ctx(), target_user_id="u-t",
                                 strategy_ids=["strat-a"], reason="x",
                                 expected_versions={"strat-a": 0})


def test_membership_and_subscription_independent(env):
    Session, svc = env
    svc.set_membership(context=_ctx(key="k-mem-0001"), target_user_id="u-t",
                       mode="permanent", valid_until=None, reason="x", expected_version=0)
    svc.set_subscriptions(context=_ctx(key="k-sub-0001"), target_user_id="u-t",
                          strategy_ids=["strat-a"], mode="permanent", valid_until=None,
                          reason="x", expected_versions={"strat-a": 0})
    # 撤销订阅不动会员
    svc.revoke_subscriptions(context=_ctx(key="k-sub-rev01"), target_user_id="u-t",
                             strategy_ids=["strat-a"], reason="x",
                             expected_versions={"strat-a": 1})
    s = Session()
    try:
        m = s.query(orm.MembershipModel).filter_by(user_id="u-t").one()
        assert m.revoked_at is None and m.is_permanent is True
    finally:
        s.close()
