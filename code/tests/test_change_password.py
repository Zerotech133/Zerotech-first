"""改密三连修（deep-test Important 3a/3b/3c）。

3a 禁写中间件白名单放行 /auth/users/{id}/password——本人（USER/VIEWER）自改
   可达端点；越权由端点 self-or-ADMIN 兜底 403。
3b 自助改密必须验旧密码（ADMIN 改他人=管理重置语义，免旧密）。
3c 改密后旧 token 一律失效：iat 早于 password_changed_at、或无 iat（旧形态
   token）→ 401 重新登录；重登新 token 正常。

隔离：sqlite in-memory（monkeypatch get_session）+ DB 模式 AuthService，
与 test_user_management.api_client 同形态；middleware 只看 token payload
role，无需打桩。
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def pw_env(monkeypatch):
    from sqlalchemy import create_engine
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
        return {"Authorization": f"Bearer {r.json()['token']}"}

    # admin 默认登录态（建用户用），与 test_user_management.api_client 同形态
    client.headers.update(_login("admin", "123456"))

    def _create(username, password, role):
        r = client.post(
            "/api/v1/auth/users",
            json={"username": username, "password": password, "role": role},
        )
        assert r.status_code == 201, r.text
        return r.json()["user_id"]

    bob_id = _create("bob", "bobpw1", "USER")
    vic_id = _create("vic", "vicpw1", "VIEWER")

    yield SimpleNamespace(
        client=client, svc=svc, repo=repo, bob_id=bob_id, vic_id=vic_id, _login=_login,
    )
    app.dependency_overrides.pop(get_auth_service, None)


def _pw_url(uid: str) -> str:
    return f"/api/v1/auth/users/{uid}/password"


# ── 3a：禁写中间件白名单（{id} 是变量，fnmatch 通配）────────────────

def test_user_can_change_own_password(pw_env):
    """USER 自改不被 user_owner_scope_only 中间件 403（端点内允许 self）。"""
    c = pw_env.client
    r = c.put(_pw_url(pw_env.bob_id), headers=pw_env._login("bob", "bobpw1"),
              json={"old_password": "bobpw1", "new_password": "bobpw2"})
    assert r.status_code == 200, r.text
    # 确认真改成功：新密码能登录
    assert pw_env._login("bob", "bobpw2")


def test_user_cannot_change_others_password(pw_env):
    """USER 改他人：中间件放行（password 在白名单）、端点 self-or-ADMIN 兜底 403。"""
    c = pw_env.client
    r = c.put(_pw_url(pw_env.vic_id), headers=pw_env._login("bob", "bobpw1"),
              json={"old_password": "bobpw1", "new_password": "hacked1"})
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == {"code": "auth.only_self_password"}
    # vic 密码未被改
    assert pw_env._login("vic", "vicpw1")


def test_viewer_can_change_own_password(pw_env):
    """VIEWER 自改不被 viewer_readonly 中间件 403（本人自助端点语义）。"""
    c = pw_env.client
    r = c.put(_pw_url(pw_env.vic_id), headers=pw_env._login("vic", "vicpw1"),
              json={"old_password": "vicpw1", "new_password": "vicpw2"})
    assert r.status_code == 200, r.text


def test_viewer_cannot_change_others_password(pw_env):
    c = pw_env.client
    r = c.put(_pw_url(pw_env.bob_id), headers=pw_env._login("vic", "vicpw1"),
              json={"old_password": "vicpw1", "new_password": "hacked1"})
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == {"code": "auth.only_self_password"}


# ── 3b：自改验旧密码；ADMIN 重置他人免旧密 ──────────────────────────

def test_change_password_without_old_password_rejected(pw_env):
    """自改不带 old_password → 400（缺参，而非默默改掉）。"""
    c = pw_env.client
    r = c.put(_pw_url(pw_env.bob_id), headers=pw_env._login("bob", "bobpw1"),
              json={"new_password": "bobpw2"})
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == {"code": "auth.old_password_required"}
    # 旧密码仍有效
    assert pw_env._login("bob", "bobpw1")


def test_change_password_wrong_old_password_rejected(pw_env):
    """旧密码错误 → 400，密码不变。"""
    c = pw_env.client
    r = c.put(_pw_url(pw_env.bob_id), headers=pw_env._login("bob", "bobpw1"),
              json={"old_password": "wrong!!", "new_password": "bobpw2"})
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == {"code": "auth.wrong_old_password"}
    assert pw_env._login("bob", "bobpw1")


def test_admin_reset_others_password_without_old(pw_env):
    """ADMIN 改他人可不带 old_password（管理重置语义）。"""
    c = pw_env.client
    admin_h = pw_env._login("admin", "123456")
    r = c.put(_pw_url(pw_env.bob_id), headers=admin_h,
              json={"new_password": "resetpw1"})
    assert r.status_code == 200, r.text
    assert pw_env._login("bob", "resetpw1")


def test_admin_change_own_password_requires_old(pw_env):
    """ADMIN 自改同样必须验旧密码（is_self 语义与角色无关）。"""
    c = pw_env.client
    admin_id = pw_env.repo.get_by_username("admin").user_id
    r = c.put(_pw_url(admin_id), headers=pw_env._login("admin", "123456"),
              json={"new_password": "adminpw2"})
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == {"code": "auth.old_password_required"}


# ── 3c：改密后旧 token 失效 ─────────────────────────────────────────

def _backoff_password_changed_at(pw_env, username, seconds=2):
    """把 password_changed_at 回拨 N 秒，消除秒级 iat 与微秒时间戳的同秒歧义
    （改密后立刻重登的 token iat=floor(now) 可能早于 now.xxxx 微秒——生产语义
    是"改密后 1 秒内重登需再登一次"，测试回拨后构造必然通过的新 token）。"""
    from datetime import timedelta

    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.repositories import user_repository as repo_mod

    s = repo_mod.get_session()
    try:
        row = s.query(orm.UserModel).filter_by(username=username).first()
        assert row.password_changed_at is not None
        row.password_changed_at = row.password_changed_at - timedelta(seconds=seconds)
        s.commit()
    finally:
        s.close()


def test_old_token_invalid_after_password_change(pw_env):
    """改密后改密前签发的 token → 401（iat 早于 password_changed_at）。"""
    c = pw_env.client
    bob_h = pw_env._login("bob", "bobpw1")
    assert c.get("/api/v1/auth/me", headers=bob_h).status_code == 200  # 改密前正常
    r = c.put(_pw_url(pw_env.bob_id), headers=bob_h,
              json={"old_password": "bobpw1", "new_password": "bobpw2"})
    assert r.status_code == 200, r.text
    assert c.get("/api/v1/auth/me", headers=bob_h).status_code == 401


def test_new_login_token_works_after_password_change(pw_env):
    """改密后重新登录的新 token 正常使用。"""
    c = pw_env.client
    bob_h = pw_env._login("bob", "bobpw1")
    r = c.put(_pw_url(pw_env.bob_id), headers=bob_h,
              json={"old_password": "bobpw1", "new_password": "bobpw2"})
    assert r.status_code == 200, r.text
    _backoff_password_changed_at(pw_env, "bob")
    new_h = pw_env._login("bob", "bobpw2")
    assert c.get("/api/v1/auth/me", headers=new_h).status_code == 200


def test_legacy_token_without_iat_rejected_after_change(pw_env):
    """无 iat 的旧形态 token：该用户存在 password_changed_at 即一律 401。"""
    import base64
    import hashlib
    import hmac
    import json
    import time

    from zhice_os_v2.application.services.token_service import _SECRET_KEY

    c = pw_env.client
    bob_h = pw_env._login("bob", "bobpw1")
    r = c.put(_pw_url(pw_env.bob_id), headers=bob_h,
              json={"old_password": "bobpw1", "new_password": "bobpw2"})
    assert r.status_code == 200, r.text

    def _b64(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).decode().rstrip("=")

    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64(json.dumps({
        "user_id": pw_env.bob_id, "username": "bob", "role": "USER",
        "exp": int(time.time()) + 3600,  # 无 iat（token_service 加 iat 前的旧形态）
    }).encode())
    sig = _b64(hmac.new(_SECRET_KEY.encode(), f"{header}.{payload}".encode(),
                        hashlib.sha256).digest())
    legacy = {"Authorization": f"Bearer {header}.{payload}.{sig}"}
    assert c.get("/api/v1/auth/me", headers=legacy).status_code == 401


def test_token_still_valid_when_password_never_changed(pw_env):
    """从未改密的用户（password_changed_at 为 NULL）：token 不受 3c 影响。"""
    c = pw_env.client
    bob_h = pw_env._login("bob", "bobpw1")
    assert c.get("/api/v1/auth/me", headers=bob_h).status_code == 200
