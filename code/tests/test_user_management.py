"""用户管理系统测试。"""
from __future__ import annotations


def test_hash_password_returns_non_empty_string():
    from zhice_os_v2.domain.security.password import hash_password
    hashed = hash_password("mysecret")
    assert isinstance(hashed, str) and len(hashed) > 10


def test_verify_password_correct():
    from zhice_os_v2.domain.security.password import hash_password, verify_password
    h = hash_password("correcthorse")
    assert verify_password("correcthorse", h) is True


def test_verify_password_wrong():
    from zhice_os_v2.domain.security.password import hash_password, verify_password
    h = hash_password("correcthorse")
    assert verify_password("wrongpassword", h) is False


def test_two_hashes_of_same_password_differ():
    from zhice_os_v2.domain.security.password import hash_password
    assert hash_password("abc") != hash_password("abc")


import pytest


@pytest.fixture()
def user_repo(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import zhice_os_v2.infrastructure.repositories.user_repository as repo_mod
    from zhice_os_v2.infrastructure.db.base import Base
    from zhice_os_v2.infrastructure.repositories.user_repository import UserRepository

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(repo_mod, "get_session", lambda: Session())
    return UserRepository()


def test_create_and_get_user(user_repo):
    from zhice_os_v2.domain.security.password import hash_password
    user = user_repo.create("u-1", "alice", hash_password("pass"), "TRADER")
    assert user.username == "alice" and user.role == "TRADER" and user.is_active is True


def test_get_by_username(user_repo):
    from zhice_os_v2.domain.security.password import hash_password
    user_repo.create("u-2", "bob", hash_password("pass"), "VIEWER")
    assert user_repo.get_by_username("bob") is not None
    assert user_repo.get_by_username("nobody") is None


def test_list_all(user_repo):
    from zhice_os_v2.domain.security.password import hash_password
    user_repo.create("u-3", "carol", hash_password("x"), "ADMIN")
    user_repo.create("u-4", "dave", hash_password("y"), "VIEWER")
    assert len(user_repo.list_all()) == 2


def test_update_role(user_repo):
    from zhice_os_v2.domain.security.password import hash_password
    user_repo.create("u-5", "eve", hash_password("p"), "VIEWER")
    updated = user_repo.update_role_protected("u-5", "TRADER")
    assert updated.role == "TRADER"
    assert user_repo.get_by_id("u-5").role == "TRADER"


def test_update_password(user_repo):
    from zhice_os_v2.domain.security.password import hash_password, verify_password
    user_repo.create("u-6", "frank", hash_password("old"), "VIEWER")
    user_repo.update_password("u-6", hash_password("new"))
    assert verify_password("new", user_repo.get_by_id("u-6").password_hash)


def test_deactivate(user_repo):
    from zhice_os_v2.domain.security.password import hash_password
    user_repo.create("u-7", "grace", hash_password("p"), "VIEWER")
    updated = user_repo.deactivate_protected("u-7")
    assert updated.is_active is False
    assert user_repo.get_by_id("u-7").is_active is False


def test_count_admins(user_repo):
    from zhice_os_v2.domain.security.password import hash_password
    user_repo.create("u-8", "henry", hash_password("p"), "ADMIN")
    user_repo.create("u-9", "iris", hash_password("p"), "VIEWER")
    assert user_repo.count_admins() == 1


def test_update_role_protected_rejects_missing_user(user_repo):
    # D10：错误码化（前端 i18n errors 命名空间 auth.user_not_found）
    with pytest.raises(ValueError, match="auth.user_not_found"):
        user_repo.update_role_protected("missing", "VIEWER")


def test_deactivate_protected_rejects_missing_user(user_repo):
    with pytest.raises(ValueError, match="auth.user_not_found"):
        user_repo.deactivate_protected("missing")


def test_last_admin_protection_in_sequence(user_repo):
    from zhice_os_v2.domain.security.password import hash_password

    user_repo.create("admin-1", "admin-1", hash_password("pass123"), "ADMIN")
    user_repo.create("admin-2", "admin-2", hash_password("pass123"), "ADMIN")

    assert user_repo.update_role_protected("admin-1", "VIEWER").role == "VIEWER"
    # D10：错误码化（auth.last_admin_protected 覆盖降级/停用两个保护分支）
    with pytest.raises(ValueError, match="auth.last_admin_protected"):
        user_repo.deactivate_protected("admin-2")
    assert user_repo.get_by_id("admin-2").is_active is True


def test_last_admin_protection_reverse_sequence(user_repo):
    from zhice_os_v2.domain.security.password import hash_password

    user_repo.create("admin-1", "admin-1", hash_password("pass123"), "ADMIN")
    user_repo.create("admin-2", "admin-2", hash_password("pass123"), "ADMIN")

    assert user_repo.deactivate_protected("admin-1").is_active is False
    with pytest.raises(ValueError, match="auth.last_admin_protected"):
        user_repo.update_role_protected("admin-2", "TRADER")
    assert user_repo.get_by_id("admin-2").role == "ADMIN"


@pytest.fixture()
def auth_service(user_repo):
    from zhice_os_v2.application.services.auth_service import AuthService
    svc = AuthService(repo=user_repo)
    svc.seed_admin("admin", "123456")
    return svc


def test_auth_service_seed_admin(auth_service):
    user = auth_service.authenticate("admin", "123456")
    assert user is not None and user.role.value == "ADMIN"


@pytest.mark.parametrize("password", ["x", "x" * 129])
def test_auth_service_seed_admin_rejects_invalid_bootstrap_password(user_repo, password):
    from zhice_os_v2.application.services.auth_service import AuthService

    service = AuthService(repo=user_repo)
    with pytest.raises(ValueError, match="密码长度必须为 6 到 128 位"):
        service.seed_admin("admin", password)
    assert user_repo.count_admins() == 0
    assert user_repo.get_by_username("admin") is None


@pytest.mark.parametrize("password", ["x", "x" * 129])
def test_prod_settings_reject_invalid_bootstrap_password_length(password):
    from pydantic import ValidationError

    from zhice_os_v2.config.settings import AppSettings

    with pytest.raises(ValidationError, match="长度必须为 6 到 128 位"):
        AppSettings(
            app_env="prod",
            secret_key="production-secret-key-at-least-strong",
            admin_password=password,
        )


def test_prod_settings_still_rejects_fixed_weak_bootstrap_password():
    from pydantic import ValidationError

    from zhice_os_v2.config.settings import AppSettings

    with pytest.raises(ValidationError, match="弱口令"):
        AppSettings(
            app_env="prod",
            secret_key="production-secret-key-at-least-strong",
            admin_password="123456",
        )


def test_auth_service_wrong_password(auth_service):
    assert auth_service.authenticate("admin", "wrong") is None


def test_auth_service_create_user(auth_service):
    user = auth_service.create_user("alice", "pass12", "TRADER")
    assert user.username == "alice" and user.role.value == "TRADER"


def test_auth_service_duplicate_username(auth_service):
    auth_service.create_user("bob", "pass23", "VIEWER")
    # D10：与 register 同键 auth.username_taken
    with pytest.raises(ValueError, match="auth.username_taken"):
        auth_service.create_user("bob", "pass34", "VIEWER")


def test_auth_service_update_role(auth_service):
    auth_service.create_user("carol", "pass45", "VIEWER")
    updated = auth_service.update_role_by_username("carol", "RESEARCHER")
    assert updated.role.value == "RESEARCHER"


def test_auth_service_change_password(auth_service):
    auth_service.create_user("dave", "oldpass", "VIEWER")
    dave = auth_service.authenticate("dave", "oldpass")
    auth_service.change_password(dave.user_id, "newpass")
    assert auth_service.authenticate("dave", "newpass") is not None
    assert auth_service.authenticate("dave", "oldpass") is None


def test_auth_service_deactivate(auth_service):
    auth_service.create_user("eve", "pass56", "VIEWER")
    eve = auth_service.authenticate("eve", "pass56")
    auth_service.deactivate_user(eve.user_id)
    assert auth_service.authenticate("eve", "pass56") is None


@pytest.mark.parametrize("password", ["", "12345", "x" * 129])
def test_auth_service_create_user_rejects_invalid_password_without_writing(auth_service, password):
    with pytest.raises(ValueError, match="密码长度必须为 6 到 128 位"):
        auth_service.create_user("bad-password", password, "VIEWER")
    assert auth_service._repo.get_by_username("bad-password") is None


@pytest.mark.parametrize("new_password", ["", "12345", "x" * 129])
def test_auth_service_change_password_rejects_invalid_password(auth_service, new_password):
    user = auth_service.create_user("password-owner", "oldpass", "VIEWER")
    with pytest.raises(ValueError, match="密码长度必须为 6 到 128 位"):
        auth_service.change_password(user.user_id, new_password)
    assert auth_service.authenticate("password-owner", "oldpass") is not None


@pytest.mark.parametrize("method", ["create", "update"])
def test_auth_service_rejects_invalid_role_without_writing(auth_service, method):
    if method == "create":
        with pytest.raises(ValueError):
            auth_service.create_user("bad-role", "pass123", "ROOT")
        assert auth_service._repo.get_by_username("bad-role") is None
        return

    user = auth_service.create_user("role-owner", "pass123", "VIEWER")
    with pytest.raises(ValueError):
        auth_service.update_role_by_id(user.user_id, "ROOT")
    assert auth_service._repo.get_by_id(user.user_id).role == "VIEWER"


def test_auth_service_memory_fallback():
    from zhice_os_v2.application.services.auth_service import AuthService
    from zhice_os_v2.config.settings import get_settings
    svc = AuthService(repo=None)
    pw = get_settings().admin_password or ""
    user = svc.authenticate("admin", pw)
    assert user is not None and user.username == "admin"


from fastapi.testclient import TestClient


@pytest.fixture()
def api_client(user_repo, monkeypatch):
    from types import SimpleNamespace

    from zhice_os_v2.api import dependencies
    from zhice_os_v2.api.main import app
    from zhice_os_v2.api.routers.auth import get_auth_service
    from zhice_os_v2.application.services.auth_service import AuthService

    svc = AuthService(repo=user_repo)
    svc.seed_admin("admin", "123456")
    app.dependency_overrides[get_auth_service] = lambda: svc
    monkeypatch.setattr(
        dependencies,
        "get_settings",
        lambda: SimpleNamespace(persistence_backend="sql"),
    )
    # B1-9：改角色端点经 AdminEntitlementService 审计事务；让其复用与 user_repo
    # 同一隔离 sqlite（repo_mod.get_session 已被 user_repo fixture patch）。
    import zhice_os_v2.application.services.admin_entitlement_service as ent_svc_mod
    import zhice_os_v2.infrastructure.repositories.admin_entitlement_repository as ent_repo_mod
    import zhice_os_v2.infrastructure.repositories.user_repository as repo_mod
    monkeypatch.setattr(ent_svc_mod, "get_session", repo_mod.get_session)
    monkeypatch.setattr(ent_repo_mod, "get_session", repo_mod.get_session)

    client = TestClient(app, raise_server_exceptions=True)
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "123456"},
    )
    assert login.status_code == 200
    client.headers.update({"Authorization": f"Bearer {login.json()['token']}"})
    yield client, svc
    app.dependency_overrides.pop(get_auth_service, None)


def test_api_list_users_admin(api_client):
    client, _ = api_client
    r = client.get("/api/v1/auth/users")
    assert r.status_code == 200 and isinstance(r.json(), list)


def test_api_create_user(api_client):
    client, _ = api_client
    r = client.post("/api/v1/auth/users", json={"username": "newuser", "password": "pass123", "role": "VIEWER"})
    assert r.status_code == 201 and r.json()["username"] == "newuser"


def test_api_create_duplicate_fails(api_client):
    client, _ = api_client
    client.post("/api/v1/auth/users", json={"username": "dup", "password": "pass123", "role": "VIEWER"})
    r = client.post("/api/v1/auth/users", json={"username": "dup", "password": "pass456", "role": "VIEWER"})
    assert r.status_code == 409


@pytest.mark.parametrize("password", ["", "12345", "x" * 129])
def test_api_create_rejects_invalid_password_without_dirty_row(api_client, password):
    client, svc = api_client
    r = client.post(
        "/api/v1/auth/users",
        json={"username": "invalid-password", "password": password, "role": "VIEWER"},
    )
    assert r.status_code == 422
    assert svc._repo.get_by_username("invalid-password") is None


def test_api_create_rejects_invalid_role_without_dirty_row(api_client):
    client, svc = api_client
    r = client.post(
        "/api/v1/auth/users",
        json={"username": "invalid-role", "password": "pass123", "role": "ROOT"},
    )
    assert r.status_code == 422
    assert svc._repo.get_by_username("invalid-role") is None


def test_api_update_role(api_client):
    client, _ = api_client
    r = client.post("/api/v1/auth/users", json={"username": "u1", "password": "pass123", "role": "VIEWER"})
    uid = r.json()["user_id"]
    r2 = client.put(f"/api/v1/auth/users/{uid}/role", json={"role": "TRADER", "auth_version": 1})
    assert r2.status_code == 200 and r2.json()["role"] == "TRADER"


def test_api_update_role_rejects_invalid_role_without_dirty_row(api_client):
    client, svc = api_client
    created = client.post(
        "/api/v1/auth/users",
        json={"username": "valid-role", "password": "pass123", "role": "VIEWER"},
    )
    uid = created.json()["user_id"]
    r = client.put(f"/api/v1/auth/users/{uid}/role", json={"role": "ROOT", "auth_version": 1})
    assert r.status_code == 422
    assert svc._repo.get_by_id(uid).role == "VIEWER"


def test_api_change_password(api_client):
    client, svc = api_client
    admin_row = svc._repo.get_by_username("admin")
    # 3b：自改（含 ADMIN）必须带 old_password；不带 → 400（deep-test 后语义）
    r = client.put(
        f"/api/v1/auth/users/{admin_row.user_id}/password",
        json={"new_password": "newpass"},
    )
    assert r.status_code == 400
    r = client.put(
        f"/api/v1/auth/users/{admin_row.user_id}/password",
        json={"old_password": "123456", "new_password": "newpass"},
    )
    assert r.status_code == 200


@pytest.mark.parametrize("new_password", ["", "12345", "x" * 129])
def test_api_change_password_rejects_invalid_password(api_client, new_password):
    client, svc = api_client
    admin_row = svc._repo.get_by_username("admin")
    old_hash = admin_row.password_hash
    r = client.put(
        f"/api/v1/auth/users/{admin_row.user_id}/password",
        json={"new_password": new_password},
    )
    assert r.status_code == 422
    assert svc._repo.get_by_id(admin_row.user_id).password_hash == old_hash


def test_api_deactivate_user(api_client):
    client, _ = api_client
    r = client.post("/api/v1/auth/users", json={"username": "todelete", "password": "pass123", "role": "VIEWER"})
    uid = r.json()["user_id"]
    r2 = client.request("DELETE", f"/api/v1/auth/users/{uid}", json={"auth_version": 1})
    assert r2.status_code == 200


def test_api_cannot_deactivate_last_admin(api_client):
    client, svc = api_client
    admin_row = svc._repo.get_by_username("admin")
    r = client.request(
        "DELETE", f"/api/v1/auth/users/{admin_row.user_id}",
        json={"auth_version": admin_row.auth_version},
    )
    # A3：最后管理员保护 → 409（乐观锁/保护类冲突统一 409）
    assert r.status_code == 409


def test_sql_token_revoked_after_role_change(api_client):
    # A3：改角色递增 auth_version → 目标旧 token 立即失效（强制重新登录）。
    client, svc = api_client
    admin_row = svc._repo.get_by_username("admin")
    svc._repo.create("u-second-admin", "second-admin", admin_row.password_hash, "ADMIN")
    svc.update_role_by_id(admin_row.user_id, "VIEWER")

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 401
    assert me.json()["detail"] == {"code": "auth.session_revoked"}


def test_sql_token_rejected_after_user_deactivation(api_client):
    client, svc = api_client
    admin_row = svc._repo.get_by_username("admin")
    svc._repo.create("u-second-admin", "second-admin", admin_row.password_hash, "ADMIN")
    svc.deactivate_user(admin_row.user_id)

    assert client.get("/api/v1/auth/me").status_code == 401


# ── A3：安全变更原子递增 auth_version + 乐观锁 ─────────────────────────

def test_role_change_increments_auth_version_once(user_repo):
    from zhice_os_v2.domain.security.password import hash_password
    user_repo.create("u-av-1", "av1", hash_password("p"), "VIEWER")
    updated = user_repo.update_role_protected("u-av-1", "TRADER", expected_auth_version=1)
    assert updated.role == "TRADER"
    assert updated.auth_version == 2


def test_role_change_rejects_stale_auth_version_without_mutation(user_repo):
    from zhice_os_v2.domain.security.password import hash_password
    user_repo.create("u-av-2", "av2", hash_password("p"), "VIEWER")
    user_repo.update_role_protected("u-av-2", "TRADER", expected_auth_version=1)
    with pytest.raises(ValueError, match="auth.auth_version_conflict"):
        user_repo.update_role_protected("u-av-2", "USER", expected_auth_version=1)
    stored = user_repo.get_by_id("u-av-2")
    assert stored.role == "TRADER"
    assert stored.auth_version == 2


def test_deactivate_increments_auth_version_once(user_repo):
    from zhice_os_v2.domain.security.password import hash_password
    user_repo.create("u-av-3", "av3", hash_password("p"), "VIEWER")
    updated = user_repo.deactivate_protected("u-av-3", expected_auth_version=1)
    assert updated.is_active is False
    assert updated.auth_version == 2


def test_password_change_increments_auth_version_once(user_repo):
    from zhice_os_v2.domain.security.password import hash_password
    user_repo.create("u-av-4", "av4", hash_password("old"), "VIEWER")
    updated = user_repo.update_password("u-av-4", hash_password("new"))
    assert updated.auth_version == 2


def test_last_admin_rejection_does_not_increment_auth_version(user_repo):
    from zhice_os_v2.domain.security.password import hash_password
    user_repo.create("only-admin", "only-admin", hash_password("p"), "ADMIN")
    before = user_repo.get_by_id("only-admin").auth_version
    with pytest.raises(ValueError, match="auth.last_admin_protected"):
        user_repo.deactivate_protected("only-admin", expected_auth_version=before)
    assert user_repo.get_by_id("only-admin").auth_version == before


def test_api_update_role_bumps_and_returns_auth_version(api_client):
    client, _ = api_client
    r = client.post("/api/v1/auth/users", json={"username": "avu1", "password": "pass123", "role": "VIEWER"})
    uid = r.json()["user_id"]
    r2 = client.put(f"/api/v1/auth/users/{uid}/role", json={"role": "TRADER", "auth_version": 1})
    assert r2.status_code == 200, r2.text
    assert r2.json()["role"] == "TRADER"
    assert r2.json()["auth_version"] == 2


def test_api_update_role_stale_version_conflicts(api_client):
    client, _ = api_client
    r = client.post("/api/v1/auth/users", json={"username": "avu2", "password": "pass123", "role": "VIEWER"})
    uid = r.json()["user_id"]
    client.put(f"/api/v1/auth/users/{uid}/role", json={"role": "TRADER", "auth_version": 1})
    stale = client.put(f"/api/v1/auth/users/{uid}/role", json={"role": "USER", "auth_version": 1})
    assert stale.status_code == 409
    assert stale.json()["detail"] == {"code": "auth.auth_version_conflict"}


def test_api_role_change_revokes_target_old_session(api_client):
    client, svc = api_client
    # 建 bob，用 bob 的 token 访问 /me（版本 1）→ 改 bob 角色后旧 token 立即失效。
    created = client.post("/api/v1/auth/users", json={"username": "bob-rev", "password": "bobpw1", "role": "USER"})
    uid = created.json()["user_id"]
    bob_login = client.post("/api/v1/auth/login", json={"username": "bob-rev", "password": "bobpw1"})
    bob_headers = {"Authorization": f"Bearer {bob_login.json()['token']}"}
    assert client.get("/api/v1/auth/me", headers=bob_headers).status_code == 200
    # admin 改 bob 角色 → auth_version 递增
    client.put(f"/api/v1/auth/users/{uid}/role", json={"role": "TRADER", "auth_version": 1})
    revoked = client.get("/api/v1/auth/me", headers=bob_headers)
    assert revoked.status_code == 401
    assert revoked.json()["detail"] == {"code": "auth.session_revoked"}


def test_api_deactivate_requires_and_bumps_auth_version(api_client):
    client, _ = api_client
    r = client.post("/api/v1/auth/users", json={"username": "avu3", "password": "pass123", "role": "VIEWER"})
    uid = r.json()["user_id"]
    r2 = client.request("DELETE", f"/api/v1/auth/users/{uid}", json={"auth_version": 1})
    assert r2.status_code == 200, r2.text
    assert r2.json()["auth_version"] == 2
