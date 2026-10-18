from fastapi.testclient import TestClient

from zhice_os_v2.api.main import app

client = TestClient(app)


def test_login_admin():
    resp = client.post("/api/v1/auth/login", json={"username": "admin", "password": ""})
    assert resp.status_code == 200
    assert resp.json()["role"] == "ADMIN"


def test_login_invalid_user():
    resp = client.post("/api/v1/auth/login", json={"username": "nobody", "password": ""})
    assert resp.status_code == 401


def test_login_invalid_credentials_code():
    """C2：401 detail 带 errors 命名空间 code（前端 i18n 翻译"用户名或密码错误"）。

    统一文案、不区分"用户不存在/密码错"——防用户名枚举。
    """
    resp = client.post("/api/v1/auth/login", json={"username": "nobody", "password": "bad"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == {"code": "auth.invalid_credentials"}


def test_list_users():
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": ""})
    token = login.json()["token"]
    resp = client.get("/api/v1/auth/users", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_list_users_requires_admin():
    resp = client.get("/api/v1/auth/users")
    assert resp.status_code == 401


def test_me_with_token():
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": ""})
    token = login.json()["token"]
    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["username"] == "admin"


def test_me_without_token():
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 401
