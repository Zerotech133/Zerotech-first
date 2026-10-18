# 用户管理系统 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建完整的多用户管理系统，支持 ADMIN/TRADER/RESEARCHER/RISK_MANAGER/VIEWER 五个角色，提供 DB 持久化、密码哈希、CRUD API 和前端管理界面。

**Architecture:** 新增 `v2_user` 表存储用户，`UserRepository`（infrastructure 层）封装 DB 操作，`AuthService` 按 persistence_backend 选择内存（测试）或 DB（生产）模式，前端在"系统与设置"的新 Tab 中展示用户列表和操作。

**Tech Stack:** SQLAlchemy Mapped ORM, PBKDF2-HMAC-SHA256（stdlib），FastAPI，Antd Table/Modal/Form，React

---

## 文件结构

**新建：**
- `src/zhice_os_v2/domain/security/password.py` — 密码哈希工具
- `src/zhice_os_v2/infrastructure/repositories/user_repository.py` — UserRepository
- `migrations/versions/0011_users_table.py` — 数据库迁移
- `frontend/src/pages/UsersPage.tsx` — 用户管理页面
- `tests/test_user_management.py` — 后端测试

**修改：**
- `src/zhice_os_v2/infrastructure/db/models.py` — 新增 UserModel
- `src/zhice_os_v2/application/services/auth_service.py` — 接入 UserRepository
- `src/zhice_os_v2/api/routers/auth.py` — 新增 CRUD 端点
- `src/zhice_os_v2/api/main.py` — lifespan 中 seed admin
- `frontend/src/pages/groups/SystemGroup.tsx` — 新增用户管理 Tab

---

### Task 1: DB 模型 + 迁移

**Files:**
- Modify: `src/zhice_os_v2/infrastructure/db/models.py`
- Create: `migrations/versions/0011_users_table.py`

- [ ] **Step 1: 在 models.py 末尾添加 UserModel**

在文件末尾添加（保留所有现有模型）：

```python
class UserModel(Base):
    __tablename__ = "v2_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(32), default="VIEWER")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
```

- [ ] **Step 2: 创建迁移文件 `migrations/versions/0011_users_table.py`**

```python
"""add v2_user table

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-30
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "v2_user",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(64), nullable=False, unique=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="VIEWER"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_v2_user_user_id", "v2_user", ["user_id"])
    op.create_index("ix_v2_user_username", "v2_user", ["username"])


def downgrade() -> None:
    op.drop_index("ix_v2_user_username", table_name="v2_user")
    op.drop_index("ix_v2_user_user_id", table_name="v2_user")
    op.drop_table("v2_user")
```

- [ ] **Step 3: 运行迁移**

```bash
cd /Users/join/Desktop/zhice/zhice-os-v2
PYTHONPATH=src .venv/bin/alembic upgrade head
```

期望输出包含：`Running upgrade 0010 -> 0011, add v2_user table`

- [ ] **Step 4: 提交**

```bash
git add src/zhice_os_v2/infrastructure/db/models.py migrations/versions/0011_users_table.py
git commit -m "feat(user-mgmt): add v2_user table and ORM model"
```

---

### Task 2: 密码工具

**Files:**
- Create: `src/zhice_os_v2/domain/security/password.py`
- Create: `tests/test_user_management.py`

- [ ] **Step 1: 写失败测试 `tests/test_user_management.py`**

```python
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
```

- [ ] **Step 2: 确认测试失败**

```bash
cd /Users/join/Desktop/zhice/zhice-os-v2
PYTHONPATH=src .venv/bin/pytest tests/test_user_management.py -v
```

期望：`ImportError` 或 `ModuleNotFoundError`

- [ ] **Step 3: 创建 `src/zhice_os_v2/domain/security/password.py`**

```python
"""PBKDF2-HMAC-SHA256 密码哈希（标准库，无额外依赖）。"""
from __future__ import annotations
import base64, hashlib, secrets

_ITERATIONS = 260_000
_SALT_BYTES = 32


def hash_password(plain: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt, _ITERATIONS)
    return base64.b64encode(salt + digest).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        raw = base64.b64decode(hashed.encode())
        salt, stored = raw[:_SALT_BYTES], raw[_SALT_BYTES:]
        digest = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt, _ITERATIONS)
        return secrets.compare_digest(digest, stored)
    except Exception:  # noqa: BLE001
        return False
```

- [ ] **Step 4: 确认测试通过**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_user_management.py -v
```

期望：`4 passed`

- [ ] **Step 5: 提交**

```bash
git add src/zhice_os_v2/domain/security/password.py tests/test_user_management.py
git commit -m "feat(user-mgmt): add PBKDF2 password hashing utility"
```

---

### Task 3: UserRepository

**Files:**
- Create: `src/zhice_os_v2/infrastructure/repositories/user_repository.py`
- Modify: `tests/test_user_management.py`

- [ ] **Step 1: 追加测试到 `tests/test_user_management.py`**

```python
import pytest


@pytest.fixture()
def user_repo(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from zhice_os_v2.infrastructure.db.base import Base
    from zhice_os_v2.infrastructure.repositories.user_repository import UserRepository
    import zhice_os_v2.infrastructure.repositories.user_repository as repo_mod

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
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
    assert user_repo.update_role("u-5", "TRADER") is True
    assert user_repo.get_by_id("u-5").role == "TRADER"


def test_update_password(user_repo):
    from zhice_os_v2.domain.security.password import hash_password, verify_password
    user_repo.create("u-6", "frank", hash_password("old"), "VIEWER")
    user_repo.update_password("u-6", hash_password("new"))
    assert verify_password("new", user_repo.get_by_id("u-6").password_hash)


def test_deactivate(user_repo):
    from zhice_os_v2.domain.security.password import hash_password
    user_repo.create("u-7", "grace", hash_password("p"), "VIEWER")
    assert user_repo.deactivate("u-7") is True
    assert user_repo.get_by_id("u-7").is_active is False


def test_count_admins(user_repo):
    from zhice_os_v2.domain.security.password import hash_password
    user_repo.create("u-8", "henry", hash_password("p"), "ADMIN")
    user_repo.create("u-9", "iris", hash_password("p"), "VIEWER")
    assert user_repo.count_admins() == 1
```

- [ ] **Step 2: 确认新测试失败**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_user_management.py -v -k "repo or create_and_get or by_username or list_all or update_role or update_password or deactivate or count_admin"
```

期望：`ModuleNotFoundError`

- [ ] **Step 3: 创建 `src/zhice_os_v2/infrastructure/repositories/user_repository.py`**

```python
"""UserRepository: v2_user 表的 CRUD 操作。"""
from __future__ import annotations
from datetime import datetime, timezone
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session


class UserRepository:
    def _s(self):
        return get_session()

    def create(self, user_id: str, username: str, password_hash: str, role: str) -> orm.UserModel:
        s = self._s()
        try:
            row = orm.UserModel(user_id=user_id, username=username, password_hash=password_hash,
                                role=role, is_active=True, created_at=datetime.now(timezone.utc))
            s.add(row); s.commit(); s.refresh(row); return row
        finally:
            s.close()

    def get_by_username(self, username: str) -> orm.UserModel | None:
        s = self._s()
        try: return s.query(orm.UserModel).filter_by(username=username, is_active=True).first()
        finally: s.close()

    def get_by_id(self, user_id: str) -> orm.UserModel | None:
        s = self._s()
        try: return s.query(orm.UserModel).filter_by(user_id=user_id).first()
        finally: s.close()

    def list_all(self) -> list[orm.UserModel]:
        s = self._s()
        try: return s.query(orm.UserModel).order_by(orm.UserModel.created_at).all()
        finally: s.close()

    def update_role(self, user_id: str, role: str) -> bool:
        s = self._s()
        try:
            n = s.query(orm.UserModel).filter_by(user_id=user_id).update({"role": role})
            s.commit(); return n > 0
        finally: s.close()

    def update_password(self, user_id: str, password_hash: str) -> bool:
        s = self._s()
        try:
            n = s.query(orm.UserModel).filter_by(user_id=user_id).update({"password_hash": password_hash})
            s.commit(); return n > 0
        finally: s.close()

    def deactivate(self, user_id: str) -> bool:
        s = self._s()
        try:
            n = s.query(orm.UserModel).filter_by(user_id=user_id).update({"is_active": False})
            s.commit(); return n > 0
        finally: s.close()

    def count_admins(self) -> int:
        s = self._s()
        try: return s.query(orm.UserModel).filter_by(role="ADMIN", is_active=True).count()
        finally: s.close()
```

- [ ] **Step 4: 确认全部测试通过**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_user_management.py -v
```

期望：`11 passed`

- [ ] **Step 5: 提交**

```bash
git add src/zhice_os_v2/infrastructure/repositories/user_repository.py tests/test_user_management.py
git commit -m "feat(user-mgmt): add UserRepository"
```

---

### Task 4: 更新 AuthService + Seed Admin

**Files:**
- Modify: `src/zhice_os_v2/application/services/auth_service.py`
- Modify: `src/zhice_os_v2/api/routers/auth.py`（get_auth_service 函数）
- Modify: `src/zhice_os_v2/api/main.py`
- Modify: `tests/test_user_management.py`

- [ ] **Step 1: 追加 AuthService 测试**

```python
@pytest.fixture()
def auth_service(user_repo):
    from zhice_os_v2.application.services.auth_service import AuthService
    svc = AuthService(repo=user_repo)
    svc.seed_admin("admin", "123456")
    return svc


def test_auth_service_seed_admin(auth_service):
    user = auth_service.authenticate("admin", "123456")
    assert user is not None and user.role.value == "ADMIN"


def test_auth_service_wrong_password(auth_service):
    assert auth_service.authenticate("admin", "wrong") is None


def test_auth_service_create_user(auth_service):
    user = auth_service.create_user("alice", "pass1", "TRADER")
    assert user.username == "alice" and user.role.value == "TRADER"


def test_auth_service_duplicate_username(auth_service):
    auth_service.create_user("bob", "pass2", "VIEWER")
    with pytest.raises(ValueError, match="用户名已存在"):
        auth_service.create_user("bob", "pass3", "VIEWER")


def test_auth_service_update_role(auth_service):
    auth_service.create_user("carol", "pass4", "VIEWER")
    updated = auth_service.update_role_by_username("carol", "RESEARCHER")
    assert updated.role.value == "RESEARCHER"


def test_auth_service_change_password(auth_service):
    auth_service.create_user("dave", "oldpass", "VIEWER")
    dave = auth_service.authenticate("dave", "oldpass")
    auth_service.change_password(dave.user_id, "newpass")
    assert auth_service.authenticate("dave", "newpass") is not None
    assert auth_service.authenticate("dave", "oldpass") is None


def test_auth_service_deactivate(auth_service):
    auth_service.create_user("eve", "pass5", "VIEWER")
    eve = auth_service.authenticate("eve", "pass5")
    auth_service.deactivate_user(eve.user_id)
    assert auth_service.authenticate("eve", "pass5") is None


def test_auth_service_memory_fallback():
    from zhice_os_v2.application.services.auth_service import AuthService
    from zhice_os_v2.config.settings import get_settings
    svc = AuthService(repo=None)
    pw = get_settings().admin_password or ""
    user = svc.authenticate("admin", pw)
    assert user is not None and user.username == "admin"
```

- [ ] **Step 2: 确认新测试失败**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_user_management.py -v -k "auth_service"
```

- [ ] **Step 3: 完整替换 `src/zhice_os_v2/application/services/auth_service.py`**

```python
"""认证服务。repo=None 时内存模式（测试）；repo=UserRepository() 时 DB 模式（生产）。"""
from __future__ import annotations
import secrets
from zhice_os_v2.domain.security.user import Role, User


class AuthService:
    def __init__(self, repo=None) -> None:
        self._repo = repo
        self._mem: dict[str, tuple[User, str]] = {}

    def _db(self) -> bool:
        return self._repo is not None

    def _row(self, row) -> User:
        return User(user_id=row.user_id, username=row.username, role=Role(row.role), is_active=row.is_active)

    def seed_admin(self, username: str = "admin", password: str = "") -> None:
        if self._db():
            if self._repo.count_admins() == 0:
                from zhice_os_v2.domain.security.password import hash_password
                self._repo.create(f"u-{secrets.token_hex(8)}", username, hash_password(password), "ADMIN")
        else:
            if username not in self._mem:
                self._mem[username] = (User(user_id="u-admin", username=username, role=Role.ADMIN), password)

    def authenticate(self, username: str, password: str = "") -> User | None:
        if self._db():
            row = self._repo.get_by_username(username)
            if row is None or not row.is_active:
                return None
            from zhice_os_v2.domain.security.password import verify_password
            return self._row(row) if verify_password(password, row.password_hash) else None
        entry = self._mem.get(username)
        if entry is None:
            return None
        user, stored_pw = entry
        from zhice_os_v2.config.settings import get_settings
        required = get_settings().admin_password or stored_pw
        return user if not required or password == required else None

    def get_user(self, user_id: str) -> User | None:
        if self._db():
            row = self._repo.get_by_id(user_id)
            return self._row(row) if row else None
        for user, _ in self._mem.values():
            if user.user_id == user_id:
                return user
        return None

    def list_users(self) -> list[User]:
        if self._db():
            return [self._row(r) for r in self._repo.list_all()]
        return [u for u, _ in self._mem.values()]

    def create_user(self, username: str, password: str, role: str) -> User:
        if not self._db():
            raise RuntimeError("create_user 仅在 DB 模式下可用")
        if self._repo.get_by_username(username) is not None:
            raise ValueError("用户名已存在")
        from zhice_os_v2.domain.security.password import hash_password
        return self._row(self._repo.create(f"u-{secrets.token_hex(8)}", username, hash_password(password), role))

    def update_role_by_username(self, username: str, role: str) -> User:
        if not self._db():
            raise RuntimeError("update_role 仅在 DB 模式下可用")
        row = self._repo.get_by_username(username)
        if row is None:
            raise ValueError(f"用户不存在: {username}")
        self._repo.update_role(row.user_id, role)
        return self._row(self._repo.get_by_id(row.user_id))

    def update_role_by_id(self, user_id: str, role: str) -> User:
        if not self._db():
            raise RuntimeError("update_role 仅在 DB 模式下可用")
        current_role = (self._repo.get_by_id(user_id) or type("", (), {"role": ""})()).role
        if current_role == "ADMIN" and role != "ADMIN" and self._repo.count_admins() <= 1:
            raise ValueError("不能降级最后一个 ADMIN")
        self._repo.update_role(user_id, role)
        return self._row(self._repo.get_by_id(user_id))

    def change_password(self, user_id: str, new_password: str) -> None:
        if not self._db():
            raise RuntimeError("change_password 仅在 DB 模式下可用")
        from zhice_os_v2.domain.security.password import hash_password
        self._repo.update_password(user_id, hash_password(new_password))

    def deactivate_user(self, user_id: str) -> None:
        if not self._db():
            raise RuntimeError("deactivate_user 仅在 DB 模式下可用")
        row = self._repo.get_by_id(user_id)
        if row and row.role == "ADMIN" and self._repo.count_admins() <= 1:
            raise ValueError("不能停用最后一个 ADMIN")
        self._repo.deactivate(user_id)
```

- [ ] **Step 4: 修改 `src/zhice_os_v2/api/routers/auth.py` 中的 `get_auth_service`**

将现有的 `get_auth_service` 函数替换为：

```python
def get_auth_service() -> AuthService:
    from zhice_os_v2.config.settings import get_settings
    if get_settings().persistence_backend == "sql":
        from zhice_os_v2.infrastructure.repositories.user_repository import UserRepository
        return AuthService(repo=UserRepository())
    return AuthService()
```

- [ ] **Step 5: 修改 `src/zhice_os_v2/api/main.py` lifespan，在 `seed_demo_data(...)` 之后添加**

```python
    from zhice_os_v2.api.routers.auth import get_auth_service
    from zhice_os_v2.config.settings import get_settings
    _auth = get_auth_service()
    _auth.seed_admin("admin", get_settings().admin_password or "123456")
```

- [ ] **Step 6: 运行全部测试**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_user_management.py -v
```

期望：`19 passed`

```bash
PYTHONPATH=src .venv/bin/pytest -m "not real_pg" -q
```

期望：`0 failed`

- [ ] **Step 7: 提交**

```bash
git add src/zhice_os_v2/application/services/auth_service.py \
        src/zhice_os_v2/api/routers/auth.py \
        src/zhice_os_v2/api/main.py \
        tests/test_user_management.py
git commit -m "feat(user-mgmt): DB-backed AuthService with seed_admin"
```

---

### Task 5: API 端点 (CRUD)

**Files:**
- Modify: `src/zhice_os_v2/api/routers/auth.py`
- Modify: `tests/test_user_management.py`

- [ ] **Step 1: 追加 API 测试**

```python
from fastapi.testclient import TestClient


@pytest.fixture()
def api_client(monkeypatch, user_repo):
    from zhice_os_v2.api.main import app
    from zhice_os_v2.api.routers import auth as auth_mod
    from zhice_os_v2.application.services.auth_service import AuthService
    from zhice_os_v2.application.services.token_service import create_token

    svc = AuthService(repo=user_repo)
    svc.seed_admin("admin", "123456")
    monkeypatch.setattr(auth_mod, "get_auth_service", lambda: svc)

    token = create_token("u-admin-test", "admin", "ADMIN")
    client = TestClient(app, raise_server_exceptions=True)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client, svc


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
    client.post("/api/v1/auth/users", json={"username": "dup", "password": "x", "role": "VIEWER"})
    r = client.post("/api/v1/auth/users", json={"username": "dup", "password": "y", "role": "VIEWER"})
    assert r.status_code == 409


def test_api_update_role(api_client):
    client, _ = api_client
    r = client.post("/api/v1/auth/users", json={"username": "u1", "password": "p", "role": "VIEWER"})
    uid = r.json()["user_id"]
    r2 = client.put(f"/api/v1/auth/users/{uid}/role", json={"role": "TRADER"})
    assert r2.status_code == 200 and r2.json()["role"] == "TRADER"


def test_api_change_password(api_client):
    client, svc = api_client
    admin_row = svc._repo.get_by_username("admin")
    r = client.put(f"/api/v1/auth/users/{admin_row.user_id}/password", json={"new_password": "newpass"})
    assert r.status_code == 200


def test_api_deactivate_user(api_client):
    client, _ = api_client
    r = client.post("/api/v1/auth/users", json={"username": "todelete", "password": "p", "role": "VIEWER"})
    uid = r.json()["user_id"]
    r2 = client.delete(f"/api/v1/auth/users/{uid}")
    assert r2.status_code == 200


def test_api_cannot_deactivate_last_admin(api_client):
    client, svc = api_client
    admin_row = svc._repo.get_by_username("admin")
    r = client.delete(f"/api/v1/auth/users/{admin_row.user_id}")
    assert r.status_code == 400
```

- [ ] **Step 2: 确认新测试失败**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_user_management.py -v -k "api_"
```

- [ ] **Step 3: 完整替换 `src/zhice_os_v2/api/routers/auth.py`**

```python
"""认证 API。"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from zhice_os_v2.api.dependencies import get_current_user, require_roles
from zhice_os_v2.application.services.auth_service import AuthService
from zhice_os_v2.application.services.token_service import create_token
from zhice_os_v2.domain.security.user import Role, User

router = APIRouter(prefix="/auth", tags=["auth"])


def get_auth_service() -> AuthService:
    from zhice_os_v2.config.settings import get_settings
    if get_settings().persistence_backend == "sql":
        from zhice_os_v2.infrastructure.repositories.user_repository import UserRepository
        return AuthService(repo=UserRepository())
    return AuthService()


class LoginRequest(BaseModel):
    username: str
    password: str = ""


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "VIEWER"


class UpdateRoleRequest(BaseModel):
    role: str


class ChangePasswordRequest(BaseModel):
    new_password: str


@router.post("/login")
def login(body: LoginRequest, service: AuthService = Depends(get_auth_service)):
    user = service.authenticate(body.username, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid credentials")
    token = create_token(user.user_id, user.username, user.role.value)
    return {"token": token, "user_id": user.user_id, "username": user.username, "role": user.role}


@router.get("/me")
def me(current_user: User = Depends(get_current_user)):
    return {"user_id": current_user.user_id, "username": current_user.username, "role": current_user.role}


@router.get("/users")
def list_users(service: AuthService = Depends(get_auth_service), _: User = Depends(require_roles(Role.ADMIN))):
    return [{"user_id": u.user_id, "username": u.username, "role": u.role, "is_active": u.is_active}
            for u in service.list_users()]


@router.post("/users", status_code=201)
def create_user(body: CreateUserRequest, service: AuthService = Depends(get_auth_service),
                _: User = Depends(require_roles(Role.ADMIN))):
    try:
        user = service.create_user(body.username, body.password, body.role)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"user_id": user.user_id, "username": user.username, "role": user.role}


@router.put("/users/{user_id}/role")
def update_role(user_id: str, body: UpdateRoleRequest, service: AuthService = Depends(get_auth_service),
                _: User = Depends(require_roles(Role.ADMIN))):
    try:
        user = service.update_role_by_id(user_id, body.role)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"user_id": user.user_id, "username": user.username, "role": user.role}


@router.put("/users/{user_id}/password")
def change_password(user_id: str, body: ChangePasswordRequest, service: AuthService = Depends(get_auth_service),
                    current_user: User = Depends(get_current_user)):
    if current_user.role != Role.ADMIN and current_user.user_id != user_id:
        raise HTTPException(status_code=403, detail="只能修改自己的密码")
    service.change_password(user_id, body.new_password)
    return {"ok": True}


@router.delete("/users/{user_id}")
def deactivate_user(user_id: str, service: AuthService = Depends(get_auth_service),
                    _: User = Depends(require_roles(Role.ADMIN))):
    try:
        service.deactivate_user(user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}
```

- [ ] **Step 4: 运行全部测试**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_user_management.py -v
```

期望：`26 passed`

```bash
PYTHONPATH=src .venv/bin/pytest -m "not real_pg" -q
```

期望：`0 failed`

- [ ] **Step 5: 提交**

```bash
git add src/zhice_os_v2/api/routers/auth.py tests/test_user_management.py
git commit -m "feat(user-mgmt): add CRUD API endpoints"
```

---

### Task 6: 前端用户管理页面

**Files:**
- Create: `frontend/src/pages/UsersPage.tsx`
- Modify: `frontend/src/pages/groups/SystemGroup.tsx`

- [ ] **Step 1: 创建 `frontend/src/pages/UsersPage.tsx`**

```tsx
import { useEffect, useState } from 'react';
import { Table, Button, Modal, Form, Input, Select, Tag, Space, Popconfirm, message } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { api } from '../services/api';

interface UserRow {
  user_id: string;
  username: string;
  role: string;
  is_active: boolean;
}

const ROLE_LABELS: Record<string, string> = {
  ADMIN: '管理员', TRADER: '交易员', RESEARCHER: '研究员',
  RISK_MANAGER: '风控', VIEWER: '只读',
};
const ROLE_COLORS: Record<string, string> = {
  ADMIN: 'red', TRADER: 'blue', RESEARCHER: 'purple',
  RISK_MANAGER: 'orange', VIEWER: 'default',
};
const ALL_ROLES = ['ADMIN', 'TRADER', 'RESEARCHER', 'RISK_MANAGER', 'VIEWER'];

export default function UsersPage() {
  const [users, setUsers] = useState<UserRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [pwTarget, setPwTarget] = useState<string | null>(null);
  const [createForm] = Form.useForm();
  const [pwForm] = Form.useForm();

  const load = async () => {
    setLoading(true);
    try { setUsers(await api.get('/auth/users')); }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  const handleCreate = async () => {
    const v = await createForm.validateFields();
    await api.post('/auth/users', v);
    message.success('用户创建成功');
    setCreateOpen(false); createForm.resetFields(); load();
  };

  const handleRole = async (user_id: string, role: string) => {
    await api.put(`/auth/users/${user_id}/role`, { role });
    message.success('角色已更新'); load();
  };

  const handleDeactivate = async (user_id: string) => {
    await api.delete(`/auth/users/${user_id}`);
    message.success('用户已停用'); load();
  };

  const handlePw = async () => {
    const v = await pwForm.validateFields();
    await api.put(`/auth/users/${pwTarget}/password`, { new_password: v.new_password });
    message.success('密码已修改'); setPwTarget(null); pwForm.resetFields();
  };

  const columns = [
    { title: '用户名', dataIndex: 'username', key: 'username' },
    {
      title: '角色', dataIndex: 'role', key: 'role',
      render: (role: string, row: UserRow) => (
        <Select value={role} size="small" style={{ width: 110 }}
          onChange={v => handleRole(row.user_id, v)}
          options={ALL_ROLES.map(r => ({ value: r, label: ROLE_LABELS[r] }))} />
      ),
    },
    {
      title: '状态', dataIndex: 'is_active', key: 'is_active',
      render: (v: boolean) => <Tag color={v ? 'green' : 'default'}>{v ? '活跃' : '停用'}</Tag>,
    },
    {
      title: '操作', key: 'actions',
      render: (_: unknown, row: UserRow) => (
        <Space>
          <Button size="small" onClick={() => setPwTarget(row.user_id)}>改密码</Button>
          {row.is_active && (
            <Popconfirm title="确认停用此用户？" onConfirm={() => handleDeactivate(row.user_id)}>
              <Button size="small" danger>停用</Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div style={{ padding: '16px 0' }}>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ fontWeight: 600, fontSize: 15 }}>用户列表</span>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新增用户</Button>
      </div>
      <Table dataSource={users} columns={columns} rowKey="user_id" loading={loading} pagination={false} size="middle" />

      <Modal title="新增用户" open={createOpen} onOk={handleCreate} onCancel={() => setCreateOpen(false)} okText="创建">
        <Form form={createForm} layout="vertical">
          <Form.Item name="username" label="用户名" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="password" label="初始密码" rules={[{ required: true, min: 6 }]}><Input.Password /></Form.Item>
          <Form.Item name="role" label="角色" initialValue="VIEWER">
            <Select options={ALL_ROLES.map(r => ({ value: r, label: ROLE_LABELS[r] }))} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal title="修改密码" open={!!pwTarget} onOk={handlePw} onCancel={() => setPwTarget(null)} okText="保存">
        <Form form={pwForm} layout="vertical">
          <Form.Item name="new_password" label="新密码" rules={[{ required: true, min: 6, message: '密码至少 6 位' }]}>
            <Input.Password />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
```

- [ ] **Step 2: 修改 `frontend/src/pages/groups/SystemGroup.tsx`**

在 import 末尾添加：
```tsx
import UsersPage from '../UsersPage';
```

在 `items` 数组最后一项 `{ key: 'exports', ... }` 之后追加：
```tsx
          { key: 'users', label: '用户管理', children: <UsersPage /> },
```

meta 字符串末尾加 ` · 用户管理`。

- [ ] **Step 3: 类型检查**

```bash
cd /Users/join/Desktop/zhice/zhice-os-v2/frontend
npx tsc --noEmit 2>&1 | head -20
```

期望：`0 errors`（无输出）

- [ ] **Step 4: 提交**

```bash
git add frontend/src/pages/UsersPage.tsx frontend/src/pages/groups/SystemGroup.tsx
git commit -m "feat(user-mgmt): add UsersPage and SystemGroup tab"
```

---

## 验收标准

1. `PYTHONPATH=src .venv/bin/pytest tests/test_user_management.py -v` → 26 passed
2. `PYTHONPATH=src .venv/bin/pytest -m "not real_pg" -q` → 0 failed
3. `cd frontend && npx tsc --noEmit` → 0 errors
4. 浏览器 系统与设置 → 用户管理 Tab 可见
5. 可创建新用户、切换角色、修改密码、停用用户
