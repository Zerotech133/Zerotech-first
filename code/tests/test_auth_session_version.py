"""单元 A Task A2：会话版本 token claim + 登录签发 + 统一身份解析兼容矩阵。

- create_token 携带整数 auth_version；登录/注册签发读数据库当前版本；
- resolve_token_identity 用数据库当前身份授权：版本谓词、缺 claim 兼容、旧角色以库为准、
  inactive 拒绝、password_changed_at 保留；
- 隔离 sqlite（monkeypatch get_session），与 test_change_password.pw_env 同形态。
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from zhice_os_v2.application.services import token_service as ts


def _sign_payload(payload: dict) -> str:
    """按 token_service 内部格式手工签发（用于构造缺 claim / 缺 iat 的旧形态 token）。"""
    import hashlib
    import hmac

    header = ts._b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = ts._b64encode(json.dumps(payload).encode())
    signing_input = f"{header}.{body}".encode()
    sig = ts._b64encode(
        hmac.new(ts._SECRET_KEY.encode(), signing_input, hashlib.sha256).digest()
    )
    return f"{header}.{body}.{sig}"


@pytest.fixture()
def sv_env(monkeypatch):
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import zhice_os_v2.infrastructure.repositories.user_repository as repo_mod
    from zhice_os_v2.api import dependencies
    from zhice_os_v2.api.main import app
    from zhice_os_v2.api.routers.auth import get_auth_service
    from zhice_os_v2.application.services.auth_service import AuthService
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
    monkeypatch.setattr(
        dependencies, "get_settings",
        lambda: SimpleNamespace(persistence_backend="sql"),
    )

    repo = UserRepository()
    svc = AuthService(repo=repo)
    svc.seed_admin("admin", "123456")
    app.dependency_overrides[get_auth_service] = lambda: svc

    client = TestClient(app, raise_server_exceptions=True)

    def _login(username, password):
        r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200, r.text
        return r.json()["token"]

    admin_token = _login("admin", "123456")
    client.headers.update({"Authorization": f"Bearer {admin_token}"})

    def _create(username, password, role):
        r = client.post(
            "/api/v1/auth/users",
            json={"username": username, "password": password, "role": role},
        )
        assert r.status_code == 201, r.text
        return r.json()["user_id"]

    bob_id = _create("bob", "bobpw1", "USER")

    def _set(uid, **cols):
        s = Session()
        try:
            assignments = ", ".join(f"{k} = :{k}" for k in cols)
            s.execute(
                text(f"UPDATE v2_user SET {assignments} WHERE user_id = :uid"),
                {**cols, "uid": uid},
            )
            s.commit()
        finally:
            s.close()

    yield SimpleNamespace(
        client=client, svc=svc, repo=repo, Session=Session,
        bob_id=bob_id, login=_login, set=_set,
    )
    # 清理依赖覆盖，避免 sql 版 AuthService 泄漏给后续测试（否则它们撞真库）。
    app.dependency_overrides.pop(get_auth_service, None)


# ── token claim / 签发 ───────────────────────────────────────────────

def test_create_token_carries_integer_auth_version():
    token = ts.create_token("u-1", "alice", "USER", auth_version=7)
    payload = ts.decode_token(token)
    assert payload is not None
    assert payload["auth_version"] == 7
    assert type(payload["auth_version"]) is int


def test_login_signs_database_current_auth_version(sv_env):
    sv_env.set(sv_env.bob_id, auth_version=4)
    token = sv_env.login("bob", "bobpw1")
    assert ts.decode_token(token)["auth_version"] == 4


def test_register_token_carries_auth_version_one(sv_env):
    r = sv_env.client.post(
        "/api/v1/auth/register",
        json={"username": "newbie", "email": "newbie@example.com", "password": "secret6"},
    )
    assert r.status_code == 201, r.text
    assert ts.decode_token(r.json()["token"])["auth_version"] == 1


# ── 统一身份解析：版本谓词与兼容矩阵 ────────────────────────────────

def _bob_headers(sv_env, *, auth_version=None, role="USER", omit_iat=False):
    payload = {
        "user_id": sv_env.bob_id,
        "username": "bob",
        "role": role,
        "exp": int(time.time()) + 3600,
    }
    if not omit_iat:
        payload["iat"] = int(time.time())
    if auth_version is not None:
        payload["auth_version"] = auth_version
    return {"Authorization": f"Bearer {_sign_payload(payload)}"}


def test_matching_auth_version_is_accepted(sv_env):
    r = sv_env.client.get("/api/v1/auth/me", headers=_bob_headers(sv_env, auth_version=1))
    assert r.status_code == 200, r.text
    assert r.json()["user_id"] == sv_env.bob_id


def test_mismatched_auth_version_returns_session_revoked(sv_env):
    sv_env.set(sv_env.bob_id, auth_version=5)
    r = sv_env.client.get("/api/v1/auth/me", headers=_bob_headers(sv_env, auth_version=1))
    assert r.status_code == 401
    assert r.json()["detail"] == {"code": "auth.session_revoked"}


def test_legacy_token_without_claim_accepted_while_version_is_one(sv_env):
    r = sv_env.client.get("/api/v1/auth/me", headers=_bob_headers(sv_env, auth_version=None))
    assert r.status_code == 200, r.text


def test_legacy_token_without_claim_rejected_after_version_increments(sv_env):
    sv_env.set(sv_env.bob_id, auth_version=2)
    r = sv_env.client.get("/api/v1/auth/me", headers=_bob_headers(sv_env, auth_version=None))
    assert r.status_code == 401
    assert r.json()["detail"] == {"code": "auth.session_revoked"}


def test_stale_token_role_uses_database_current_role(sv_env):
    # token 声称 ADMIN，但数据库 bob 仍是 USER → /me 必须回库中当前角色 USER。
    r = sv_env.client.get(
        "/api/v1/auth/me", headers=_bob_headers(sv_env, auth_version=1, role="ADMIN")
    )
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "USER"


def test_inactive_database_user_is_rejected(sv_env):
    sv_env.set(sv_env.bob_id, is_active=0)
    r = sv_env.client.get("/api/v1/auth/me", headers=_bob_headers(sv_env, auth_version=1))
    assert r.status_code == 401
    assert r.json()["detail"] == "user unavailable"


def test_password_changed_at_check_remains_effective(sv_env):
    # 改密时刻晚于 token iat → 旧 token 失效（保留 3c 语义），版本匹配也不放行。
    future = "2999-01-01 00:00:00+00:00"
    sv_env.set(sv_env.bob_id, password_changed_at=future)
    r = sv_env.client.get("/api/v1/auth/me", headers=_bob_headers(sv_env, auth_version=1))
    assert r.status_code == 401
    assert r.json()["detail"] == {"code": "auth.token_stale_after_password_change"}


# ── A4：写限制中间件用数据库当前角色（非 token role）────────────────────

def test_write_guard_uses_database_viewer_role_not_token_role(sv_env):
    # token 声称 ADMIN，但数据库 bob 是 VIEWER → 全局写被中间件按库角色 403 只读。
    sv_env.set(sv_env.bob_id, role="VIEWER")
    r = sv_env.client.post(
        "/api/v1/trade/simulate",
        headers=_bob_headers(sv_env, auth_version=1, role="ADMIN"),
        json={},
    )
    assert r.status_code == 403
    assert r.json()["detail"] == {"code": "auth.viewer_readonly"}


def test_write_guard_uses_database_user_scope_not_token_role(sv_env):
    # token 声称 ADMIN，但数据库 bob 是 USER → 全局（非 owner）写 403 owner-scope。
    r = sv_env.client.post(
        "/api/v1/trade/simulate",
        headers=_bob_headers(sv_env, auth_version=1, role="ADMIN"),
        json={},
    )
    assert r.status_code == 403
    assert r.json()["detail"] == {"code": "auth.user_owner_scope_only"}


def test_write_guard_allows_database_admin_despite_token_viewer(sv_env):
    # token 声称 VIEWER，但数据库 bob 是 ADMIN → 中间件放行（下游按需处理，绝非只读 403）。
    sv_env.set(sv_env.bob_id, role="ADMIN")
    r = sv_env.client.post(
        "/api/v1/trade/simulate",
        headers=_bob_headers(sv_env, auth_version=1, role="VIEWER"),
        json={},
    )
    assert r.json().get("detail") != {"code": "auth.viewer_readonly"}
    assert r.json().get("detail") != {"code": "auth.user_owner_scope_only"}


def test_write_guard_stale_version_admin_token_is_401(sv_env):
    # token 声称 ADMIN 且版本 1，但数据库版本已递增 → 会话失效 401（不按旧角色放行）。
    sv_env.set(sv_env.bob_id, role="ADMIN", auth_version=9)
    r = sv_env.client.post(
        "/api/v1/trade/simulate",
        headers=_bob_headers(sv_env, auth_version=1, role="ADMIN"),
        json={},
    )
    assert r.status_code == 401
    assert r.json()["detail"] == {"code": "auth.session_revoked"}


def test_identity_resolved_once_per_write_request(sv_env, monkeypatch):
    # 中间件与端点依赖共享 request.state 缓存 → 单请求只读一次数据库身份。
    import zhice_os_v2.infrastructure.repositories.user_repository as repo_mod

    calls = {"n": 0}
    original = repo_mod.UserRepository.get_by_id

    def _counting(self, user_id):
        calls["n"] += 1
        return original(self, user_id)

    monkeypatch.setattr(repo_mod.UserRepository, "get_by_id", _counting)
    sv_env.set(sv_env.bob_id, role="ADMIN")
    # 选一个既过写限制中间件、又走 Depends(get_current_user) 的写端点。
    sv_env.client.post(
        "/api/v1/auth/users",
        headers=_bob_headers(sv_env, auth_version=1, role="ADMIN"),
        json={"username": "made-by-bob", "password": "pass123", "role": "VIEWER"},
    )
    assert calls["n"] == 1
