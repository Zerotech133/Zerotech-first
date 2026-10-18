"""单元 B Task B2-2：开仓提交门禁（锁序 user→membership→instance）。

隔离内存 sqlite（StaticPool），env 范式同 tests/test_membership_revocation_opening_risk.py。
guard 用 domain is_member_tier_active 直接读库 membership 行判定（不受 conftest autouse
_default_active_membership monkeypatch 影响），故各场景须在库里真实写 membership 行。

覆盖：
- revoked 会员 + 自建私有 enabled → opening.membership_required
- disabled 实例 → opening.instance_disabled
- 有效会员 + enabled 自建私有 → 正常 yield（membership_active True, is_platform False）
- 平台公共(owner=None/PUBLIC) enabled + 无/过期会员 → 正常 yield（is_platform True）
- 实例不存在 → opening.instance_not_found
- 过期会员 + 自建私有 → opening.membership_required
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.application.services.opening_submission_guard import (
    OpeningSubmissionDenied,
    OpeningSubmissionGuard,
)
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.base import Base

NOW = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)


@pytest.fixture()
def Session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def guard(Session):
    return OpeningSubmissionGuard(session_factory=lambda: Session(), clock=lambda: NOW)


# ── 数据播种 ─────────────────────────────────────────────────────────

def _user(s, uid):
    s.add(orm.UserModel(user_id=uid, username=uid, password_hash="x", role="USER",
                        is_active=True, created_at=NOW))


def _membership(s, uid, *, tier, valid_until=None, is_permanent=False,
                revoked_at=None, revoked_by=None, status="active"):
    s.add(orm.MembershipModel(
        user_id=uid, tier=tier, valid_until=valid_until, status=status,
        is_permanent=is_permanent, revoked_at=revoked_at,
        revoked_by_user_id=revoked_by, version=1, created_at=NOW, updated_at=NOW,
    ))


def _strategy(s, sid, owner, vis, pub):
    s.add(orm.StrategyModel(
        strategy_id=sid, strategy_name=sid, market="US",
        status="ACTIVE", owner_user_id=owner,
        visibility=vis, publication_status=pub, created_at=NOW, updated_at=NOW,
    ))


def _version(s, svid, sid):
    s.add(orm.StrategyVersionModel(
        strategy_version_id=svid, strategy_id=sid, version_no="1", market="US",
        status="ACTIVE", spec={}, created_at=NOW,
    ))


def _account(s, aid, uid):
    from decimal import Decimal
    s.add(orm.UserAccountModel(
        account_id=aid, user_id=uid, market="US", currency="USD",
        account_type="SHARED_PORTFOLIO", initial_capital=Decimal("100000"),
        cash_balance=Decimal("10000"), reserved_cash=Decimal("0"),
        status="ACTIVE", created_at=NOW, updated_at=NOW,
    ))


def _instance(s, usid, uid, svid, aid, enabled):
    from decimal import Decimal
    s.add(orm.UserStrategyInstanceModel(
        user_strategy_id=usid, user_id=uid, strategy_version_id=svid, account_id=aid,
        enabled=enabled, auto_execute=False, execution_mode="MANUAL",
        max_position_ratio=Decimal("0.1"), created_at=NOW, updated_at=NOW,
    ))


def _seed_private(Session, *, enabled=True, membership_kwargs=None):
    """u-t 自建私有 strat-priv + enabled 实例 inst-priv；membership_kwargs 非空则写会员行。"""
    s = Session()
    _user(s, "u-t")
    if membership_kwargs is not None:
        _membership(s, "u-t", **membership_kwargs)
    _strategy(s, "strat-priv", "u-t", "PRIVATE", "PRIVATE")
    _version(s, "sv-priv", "strat-priv")
    _account(s, "acc-1", "u-t")
    _instance(s, "inst-priv", "u-t", "sv-priv", "acc-1", enabled=enabled)
    s.commit()
    s.close()


def _seed_platform(Session, *, membership_kwargs=None):
    """u-t 运行平台公共 strat-pub(owner=None/PUBLIC) enabled 实例 inst-pub。"""
    s = Session()
    _user(s, "u-t")
    if membership_kwargs is not None:
        _membership(s, "u-t", **membership_kwargs)
    _strategy(s, "strat-pub", None, "PUBLIC", "PUBLISHED")
    _version(s, "sv-pub", "strat-pub")
    _account(s, "acc-1", "u-t")
    _instance(s, "inst-pub", "u-t", "sv-pub", "acc-1", enabled=True)
    s.commit()
    s.close()


# ── 测试 ─────────────────────────────────────────────────────────────

def test_revoked_member_private_denied(Session, guard):
    _seed_private(Session, membership_kwargs=dict(
        tier="member", valid_until=NOW + timedelta(days=30),
        revoked_at=NOW - timedelta(days=1), revoked_by="admin", status="revoked"))
    with pytest.raises(OpeningSubmissionDenied) as ei, guard.hold(user_strategy_id="inst-priv"):
        pass
    assert ei.value.code == "opening.membership_required"


def test_disabled_instance_denied(Session, guard):
    _seed_private(Session, enabled=False, membership_kwargs=dict(
        tier="member", valid_until=NOW + timedelta(days=30)))
    with pytest.raises(OpeningSubmissionDenied) as ei, guard.hold(user_strategy_id="inst-priv"):
        pass
    assert ei.value.code == "opening.instance_disabled"


def test_active_member_private_yields(Session, guard):
    _seed_private(Session, membership_kwargs=dict(
        tier="member", valid_until=NOW + timedelta(days=30)))
    with guard.hold(user_strategy_id="inst-priv") as snap:
        assert snap.user_strategy_id == "inst-priv"
        assert snap.user_id == "u-t"
        assert snap.account_id == "acc-1"
        assert snap.is_platform_strategy is False
        assert snap.membership_active is True


def test_platform_public_no_membership_yields(Session, guard):
    # 平台公共实例 + 完全无会员行 → 免会员放行。
    _seed_platform(Session, membership_kwargs=None)
    with guard.hold(user_strategy_id="inst-pub") as snap:
        assert snap.is_platform_strategy is True
        assert snap.membership_active is False


def test_platform_public_expired_membership_yields(Session, guard):
    # 平台公共实例 + 过期会员 → 仍放行。
    _seed_platform(Session, membership_kwargs=dict(
        tier="member", valid_until=NOW - timedelta(days=1), status="active"))
    with guard.hold(user_strategy_id="inst-pub") as snap:
        assert snap.is_platform_strategy is True
        assert snap.membership_active is False


def test_instance_not_found_denied(Session, guard):
    _seed_private(Session, membership_kwargs=dict(
        tier="member", valid_until=NOW + timedelta(days=30)))
    with pytest.raises(OpeningSubmissionDenied) as ei, guard.hold(
        user_strategy_id="does-not-exist"
    ):
        pass
    assert ei.value.code == "opening.instance_not_found"


def test_expired_member_private_denied(Session, guard):
    _seed_private(Session, membership_kwargs=dict(
        tier="member", valid_until=NOW - timedelta(days=1), status="active"))
    with pytest.raises(OpeningSubmissionDenied) as ei, guard.hold(user_strategy_id="inst-priv"):
        pass
    assert ei.value.code == "opening.membership_required"
