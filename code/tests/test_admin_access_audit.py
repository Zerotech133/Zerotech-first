"""管理员全局访问审计单测（严格 TDD）。

规格（specs 第 317 行）：管理员全局查看必须走显式 admin API，并写入持久化
v2_access_audit（actor_user_id/action/resource_type/resource_id/request_id/created_at；
**绝不**保存密码/Token/敏感响应正文）。

隔离范式：账户/实例/审计三仓储共用同一内存 sqlite（monkeypatch 各仓储模块的 get_session，
覆盖 conftest 的 _guard_real_pg 真库守卫）；策略走内存 PersistencePort（get_db）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from zhice_os_v2.api.main import app
from zhice_os_v2.application.services.token_service import create_token


@pytest.fixture
def _admin_env(monkeypatch):
    """建内存 sqlite 全表；把 account/instance/audit 三仓储的 get_session 指向该库。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import zhice_os_v2.infrastructure.repositories.access_audit_repository as audit_mod
    import zhice_os_v2.infrastructure.repositories.user_account_repository as acc_mod
    import zhice_os_v2.infrastructure.repositories.user_strategy_repository as inst_mod
    from zhice_os_v2.infrastructure.db.base import Base
    from zhice_os_v2.infrastructure.repositories.access_audit_repository import (
        AccessAuditRepository,
    )

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(acc_mod, "get_session", lambda: Session())
    monkeypatch.setattr(inst_mod, "get_session", lambda: Session())
    monkeypatch.setattr(audit_mod, "get_session", lambda: Session())
    return AccessAuditRepository()


@pytest.fixture
def audit_repo(_admin_env):
    return _admin_env


@pytest.fixture
def client(_admin_env):
    return TestClient(app)


def _h(user_id, username, role):
    return {"Authorization": f"Bearer {create_token(user_id, username, role)}"}


@pytest.fixture
def admin_headers():
    return _h("u-admin", "admin", "ADMIN")


def test_admin_global_read_writes_audit(client, admin_headers, audit_repo):
    response = client.get(
        "/api/v1/admin/strategy-instances", headers=admin_headers
    )
    assert response.status_code == 200, response.text
    audit = audit_repo.latest()
    assert audit is not None
    assert audit.actor_user_id == "u-admin"
    assert audit.action == "LIST_GLOBAL"
    assert audit.resource_type == "user_strategy_instance"
    assert audit.request_id  # 非空


def test_audit_does_not_store_sensitive_body(client, admin_headers, audit_repo):
    client.get("/api/v1/admin/strategy-instances", headers=admin_headers)
    audit = audit_repo.latest()
    serialized = audit.to_dict()
    assert "token" not in serialized
    assert "password" not in serialized
    assert "response_body" not in serialized


def test_admin_accounts_and_strategies_write_audit(client, admin_headers, audit_repo):
    r1 = client.get("/api/v1/admin/accounts", headers=admin_headers)
    assert r1.status_code == 200, r1.text
    a1 = audit_repo.latest()
    assert a1.resource_type == "user_account"
    assert a1.action == "LIST_GLOBAL"

    r2 = client.get("/api/v1/admin/strategies", headers=admin_headers)
    assert r2.status_code == 200, r2.text
    a2 = audit_repo.latest()
    assert a2.resource_type == "strategy"
    assert a2.action == "LIST_GLOBAL"


@pytest.mark.parametrize("role", ["USER", "VIEWER", "TRADER"])
def test_non_admin_forbidden_on_admin_routes(client, role):
    r = client.get(
        "/api/v1/admin/strategy-instances",
        headers=_h("u-x", "someone", role),
    )
    assert r.status_code == 403


def test_read_only_audit_failure_fails_open(client, admin_headers, monkeypatch):
    """只读全局 dashboard：审计写入失败 fail-open（仍返回 200，ERROR 日志）。"""
    import zhice_os_v2.api.dependencies as deps

    class _BoomRepo:
        def record(self, audit):
            raise RuntimeError("audit db down")

    monkeypatch.setattr(deps, "AccessAuditRepository", lambda: _BoomRepo())
    r = client.get("/api/v1/admin/strategy-instances", headers=admin_headers)
    assert r.status_code == 200, r.text
