"""单元 B Task B1-3：AdminEntitlementRepository 幂等查询 + 审计分页/最近。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.domain.billing.admin_entitlement import AuditQuery
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.base import Base
from zhice_os_v2.infrastructure.repositories.admin_entitlement_repository import (
    AdminEntitlementRepository,
)

BASE = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)


@pytest.fixture()
def repo_env():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    repo = AdminEntitlementRepository(session_factory=lambda: Session())
    return Session, repo


def _op(s, **kw):
    defaults = dict(
        operation_id="op", actor_user_id="admin", idempotency_key="k",
        request_fingerprint="fp", action="membership.set", target_user_id="t",
        response_status=200, response_body={"ok": True}, created_at=BASE,
    )
    defaults.update(kw)
    s.add(orm.AdminOperationModel(**defaults))


def _audit(s, **kw):
    defaults = dict(
        audit_id="a", operation_id=None, actor_user_id="admin", target_user_id="t",
        source_type="ADMIN", action="membership.set", resource_type="MEMBERSHIP",
        resource_id=None, reason="r", before_state={}, after_state={},
        source_order_id=None, created_at=BASE,
    )
    defaults.update(kw)
    s.add(orm.EntitlementAuditModel(**defaults))


def test_get_operation_roundtrip(repo_env):
    Session, repo = repo_env
    s = Session()
    _op(s, operation_id="op-1", idempotency_key="key-1", response_body={"x": 1})
    s.commit()
    s.close()
    got = repo.get_operation("admin", "key-1")
    assert got is not None
    assert got.operation_id == "op-1"
    assert got.response_body == {"x": 1}
    assert repo.get_operation("admin", "nope") is None


def test_recent_audits_capped_and_sorted(repo_env):
    Session, repo = repo_env
    s = Session()
    for i in range(25):
        _audit(s, audit_id=f"a-{i:02d}", created_at=BASE + timedelta(minutes=i))
    s.commit()
    s.close()
    rows = repo.recent_audits("t", limit=20)
    assert len(rows) == 20
    # created_at DESC → 最新在前
    assert rows[0].audit_id == "a-24"
    assert rows[-1].audit_id == "a-05"


def test_recent_audits_stable_sort_same_time(repo_env):
    Session, repo = repo_env
    s = Session()
    for aid in ("a-1", "a-2", "a-3"):
        _audit(s, audit_id=aid, created_at=BASE)
    s.commit()
    s.close()
    rows = repo.recent_audits("t")
    assert [r.audit_id for r in rows] == ["a-3", "a-2", "a-1"]  # audit_id DESC 稳定


def test_list_audits_strategy_filter_maps_resource_id(repo_env):
    Session, repo = repo_env
    s = Session()
    _audit(s, audit_id="sub-a", resource_type="SUBSCRIPTION", resource_id="strat-A")
    _audit(s, audit_id="sub-b", resource_type="SUBSCRIPTION", resource_id="strat-B")
    s.commit()
    s.close()
    rows, total = repo.list_audits(AuditQuery(strategy_id="strat-A"))
    assert total == 1
    assert rows[0].resource_id == "strat-A"


@pytest.mark.parametrize("field,value,match", [
    ("actor_user_id", "other", "a-other"),
    ("action", "role.change", "a-role"),
    ("resource_type", "ROLE", "a-role"),
])
def test_list_audits_filters(repo_env, field, value, match):
    Session, repo = repo_env
    s = Session()
    _audit(s, audit_id="a-role", action="role.change", resource_type="ROLE",
           actor_user_id="admin")
    _audit(s, audit_id="a-other", actor_user_id="other")
    s.commit()
    s.close()
    rows, total = repo.list_audits(AuditQuery(**{field: value}))
    assert total == 1
    assert rows[0].audit_id == match


def test_list_audits_time_range_inclusive(repo_env):
    Session, repo = repo_env
    s = Session()
    _audit(s, audit_id="a-early", created_at=BASE)
    _audit(s, audit_id="a-late", created_at=BASE + timedelta(hours=2))
    s.commit()
    s.close()
    rows, total = repo.list_audits(AuditQuery(from_at=BASE, to_at=BASE))
    assert total == 1 and rows[0].audit_id == "a-early"  # 闭区间含端点


def test_list_audits_pagination(repo_env):
    Session, repo = repo_env
    s = Session()
    for i in range(10):
        _audit(s, audit_id=f"a-{i:02d}", created_at=BASE + timedelta(minutes=i))
    s.commit()
    s.close()
    rows, total = repo.list_audits(AuditQuery(page=2, page_size=4))
    assert total == 10
    assert len(rows) == 4


def test_payment_source_order_unique(repo_env):
    from sqlalchemy.exc import IntegrityError

    Session, _ = repo_env
    s = Session()
    _audit(s, audit_id="p-1", source_type="PAYMENT", actor_user_id=None,
           source_order_id="order-1")
    s.commit()
    _audit(s, audit_id="p-2", source_type="PAYMENT", actor_user_id=None,
           source_order_id="order-1")
    with pytest.raises(IntegrityError):
        s.commit()
    s.close()
