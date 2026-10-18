# 用户策略与账户基础 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `USER` 业务角色、公共/私有策略归属、不可变版本、持久化用户账户和用户策略运行实例，同时保持旧执行链默认不变。

**Architecture:** 以 `Strategy/StrategyVersion` 作为共享定义，以新建的 `UserAccount/UserStrategyInstance` 作为 owner 边界。所有 `/my/*` API 从 JWT 取 user_id；本阶段仅建立资源和 shadow 查询，不让运行实例产生真实订单。

**Tech Stack:** FastAPI、SQLAlchemy 2、Alembic、PostgreSQL、Pydantic、pytest。

---

## 文件结构

**Create:**
- `migrations/versions/0016_user_role_add_user.py` — 仅替换角色 CHECK 约束（加 USER）。
- `migrations/versions/0017_strategy_ownership.py` — v2_strategy 归属列 + v2_strategy_publication_request 表。
- `migrations/versions/0018_user_account.py` — v2_user_account 表。
- `migrations/versions/0019_user_strategy_instance.py` — v2_user_strategy_instance 表。
- `migrations/versions/0020_access_audit.py` — v2_access_audit 表。

> **迁移策略（复核修正）**：每个任务用**独立** revision，`down_revision` 指向前一个，链为 0015→0016→0017→0018→0019→0020。**绝不**把多张表塞进同一 revision——Alembic 按 revision 号判定已应用，同号追加在已升级环境不会重跑，会导致缺表。Task 1 已产出 0016（文件名以实际为准，本任务 commit 48952c4 用的是 `0016_multi_user_strategy_foundation.py`，后续沿用该文件名即可，不改名）。Phase B/C/D 的迁移号顺延（从 0021 起），届时再更新对应计划。
- `src/zhice_os_v2/domain/accounts/user_account.py` — 账户类型和领域对象。
- `src/zhice_os_v2/domain/strategy/user_strategy.py` — 运行实例领域对象和默认偏好。
- `src/zhice_os_v2/infrastructure/repositories/user_account_repository.py` — owner-scoped 账户持久化。
- `src/zhice_os_v2/infrastructure/repositories/user_strategy_repository.py` — owner-scoped 运行实例持久化。
- `src/zhice_os_v2/infrastructure/repositories/access_audit_repository.py` — 管理员全局访问审计持久化。
- `src/zhice_os_v2/application/services/user_account_service.py` — 账户创建与 owner 校验。
- `src/zhice_os_v2/application/services/user_strategy_service.py` — 使用公共策略、绑定账户、启停实例。
- `src/zhice_os_v2/application/services/strategy_catalog_service.py` — 公共目录与私有策略权限。
- `src/zhice_os_v2/api/routers/my_accounts.py` — `/my/accounts`。
- `src/zhice_os_v2/api/routers/my_strategies.py` — `/my/strategies` 和目录。
- `src/zhice_os_v2/api/routers/my_strategy_instances.py` — `/my/strategy-instances`。
- `tests/factories/multi_user.py` — 合成用户/策略/账户/实例/交易图工厂，禁止连接生产库。
- `tests/test_strategy_ownership.py`
- `tests/test_user_accounts.py`
- `tests/test_user_strategy_instances.py`
- `tests/test_strategy_version_immutability.py`
- `tests/test_admin_access_audit.py`

**Modify:**
- `src/zhice_os_v2/domain/security/user.py` — `Role.USER`。
- `src/zhice_os_v2/domain/security/permissions.py` — USER 权限集合。
- `src/zhice_os_v2/application/services/auth_service.py:115-146` — 注册硬编码 USER。
- `src/zhice_os_v2/api/main.py:115-135` — VIEWER 全局只读；USER 写请求仅允许 `/api/v1/my/*` 与本人 auth 自助端点。
- `src/zhice_os_v2/infrastructure/db/models.py` — ORM 映射。
- `src/zhice_os_v2/application/services/strategy_service.py` — owner、公开/私有、版本复制。
- `src/zhice_os_v2/api/routers/strategy.py` — 旧全局写 API 限 ADMIN/运营角色。
- `src/zhice_os_v2/api/main.py` — 注册新 routers。
- `src/zhice_os_v2/config/settings.py` — `multi_user_execution_mode` 默认 legacy。
- `.env.example`, `.env.prod.example`, `docker-compose.prod.yml` — 开关透传。
- `tests/test_auth_register.py`, `tests/test_rbac_viewer.py`, `tests/test_user_management.py`。

### Task 1: 新增 USER 角色并保持 VIEWER 严格只读

- [ ] **Step 1: 建立共享合成测试工厂**

Create `tests/factories/multi_user.py` with deterministic constructors (no production DB):

```python
@dataclass(frozen=True)
class SyntheticUsers:
    admin: User
    alice: User
    bob: User


def make_users() -> SyntheticUsers:
    return SyntheticUsers(
        admin=User("u-admin", "admin", Role.ADMIN),
        alice=User("u-alice", "alice", Role.USER),
        bob=User("u-bob", "bob", Role.USER),
    )
```

In later tasks extend this factory with `make_public_strategy()`, `make_private_strategy(owner)`, `make_account(owner, type, capital)`, `make_instance(owner, version, account)` and `make_owned_trade_graph(instance)`. Register pytest fixtures in `tests/conftest.py`; each fixture uses the isolated test DB/session supplied by existing conftest guards.

- [ ] **Step 2: 修改注册角色测试为 USER，并增加角色矩阵测试**

Modify `tests/test_auth_register.py`：

```python
def test_register_defaults_user():
    svc, repo = _svc()
    user = svc.register("alice", "alice@example.com", "secret6")
    assert user.role == Role.USER
    assert repo.create.call_args.args[3] == Role.USER.value


def test_register_signature_does_not_accept_role():
    svc, _ = _svc()
    with pytest.raises(TypeError):
        svc.register("alice", "a@example.com", "secret6", role="ADMIN")
```

Add to `tests/test_rbac_viewer.py`：

```python
def test_user_can_write_owner_scoped_path():
    response = client.post(
        "/api/v1/my/strategy-instances",
        headers=_h("u-user", "alice", "USER"),
        json={},
    )
    assert response.json().get("detail") != {"code": "auth.user_owner_scope_only"}


def test_user_cannot_write_global_path():
    response = client.post(
        "/api/v1/trade/simulate",
        headers=_h("u-user", "alice", "USER"),
        json={},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == {"code": "auth.user_owner_scope_only"}
```

- [ ] **Step 2: 运行 RED**

```bash
uv run pytest -q tests/test_auth_register.py tests/test_rbac_viewer.py
```

Expected: `Role.USER` 不存在或注册仍返回 VIEWER。

- [ ] **Step 3: 实现 USER 角色**

Modify `src/zhice_os_v2/domain/security/user.py`：

```python
class Role(StrEnum):
    ADMIN = "ADMIN"
    USER = "USER"
    TRADER = "TRADER"
    RESEARCHER = "RESEARCHER"
    RISK_MANAGER = "RISK_MANAGER"
    VIEWER = "VIEWER"
```

Modify `src/zhice_os_v2/domain/security/permissions.py`：

```python
PERMISSIONS = {
    Role.VIEWER: {"view"},
    Role.USER: {"view", "manage_own"},
    Role.RESEARCHER: {"view", "research"},
    Role.TRADER: {"view", "trade"},
    Role.RISK_MANAGER: {"view", "approve", "risk"},
    Role.ADMIN: {"view", "trade", "approve", "research", "risk", "admin"},
}
```

Modify `AuthService.register()` to pass `Role.USER.value`. Do not accept `role` in `RegisterRequest` or `register()`.

- [ ] **Step 4: 在 0016 替换角色约束**

```python
revision = "0016"
down_revision = "0015"
ROLE_CHECK = "role IN ('ADMIN','USER','TRADER','RESEARCHER','RISK_MANAGER','VIEWER')"

def upgrade() -> None:
    op.drop_constraint("ck_v2_user_role", "v2_user", type_="check")
    op.create_check_constraint("ck_v2_user_role", "v2_user", ROLE_CHECK)
```

Downgrade must first fail if USER rows exist, then restore the old five-role check; do not silently coerce USER to VIEWER.

- [ ] **Step 5: 运行 GREEN**

```bash
uv run pytest -q tests/test_auth_register.py tests/test_rbac_viewer.py tests/test_user_management.py
```

Expected: PASS；注册不可提权；VIEWER 仍被所有非 auth 自助写入拦截；USER 只允许 `/api/v1/my/*` 与本人 auth 自助写入，不能调用全局交易/策略管理写端点。

- [ ] **Step 6: 提交（经用户授权）**

```bash
git add migrations/versions/0016_multi_user_strategy_foundation.py \
  src/zhice_os_v2/domain/security src/zhice_os_v2/application/services/auth_service.py \
  tests/test_auth_register.py tests/test_rbac_viewer.py tests/test_user_management.py
git commit -m "feat(auth): add owner-scoped user role"
```

### Task 2: 扩展策略归属和发布状态

- [ ] **Step 1: 写 owner/visibility 失败测试**

Create `tests/test_strategy_ownership.py`：

```python
def test_catalog_contains_public_and_own_private_only(strategy_catalog, users):
    strategy_catalog.create_public("public-1", market="CN")
    strategy_catalog.create_private(users.alice, "alice-private", market="CN")
    strategy_catalog.create_private(users.bob, "bob-private", market="CN")
    ids = {item.strategy_id for item in strategy_catalog.list_visible(users.alice)}
    assert ids == {"public-1", "alice-private"}


def test_non_owner_cannot_update_private_strategy(strategy_catalog, users):
    strategy_catalog.create_private(users.alice, "alice-private", market="CN")
    with pytest.raises(PermissionError):
        strategy_catalog.update_private(users.bob, "alice-private", description="x")


def test_publish_approval_derives_public_copy(strategy_catalog, users):
    private = strategy_catalog.create_private(users.alice, "alpha", market="US")
    request = strategy_catalog.request_publication(users.alice, private.strategy_id)
    public_version = strategy_catalog.approve_publication(request.request_id, actor=users.admin)
    public = strategy_catalog.get_strategy(public_version.strategy_id)
    assert public.owner_user_id is None
    assert public.visibility == "PUBLIC"
    assert private.visibility == "PRIVATE"
    assert private.strategy_id != public.strategy_id
```

- [ ] **Step 2: 运行 RED**

```bash
uv run pytest -q tests/test_strategy_ownership.py
```

Expected: collection or test failure naming missing `StrategyCatalogService`, `owner_user_id` or `visibility`.

- [ ] **Step 3: 创建迁移 0017（down_revision=0016）增加 additive strategy columns**

```python
op.add_column("v2_strategy", sa.Column("owner_user_id", sa.String(64), nullable=True))
op.add_column("v2_strategy", sa.Column("visibility", sa.String(16), nullable=False, server_default="PUBLIC"))
op.add_column("v2_strategy", sa.Column("publication_status", sa.String(24), nullable=False, server_default="PUBLISHED"))
op.create_index("ix_v2_strategy_owner", "v2_strategy", ["owner_user_id"])
op.create_check_constraint("ck_v2_strategy_visibility", "v2_strategy", "visibility IN ('PUBLIC','PRIVATE')")
op.create_check_constraint(
    "ck_v2_strategy_publication", "v2_strategy",
    "publication_status IN ('PRIVATE','PENDING_REVIEW','PUBLISHED','REJECTED')",
)
```

Existing rows intentionally backfill as platform PUBLIC/PUBLISHED.

- [ ] **Step 4: 映射 ORM 并实现 StrategyCatalogService**

`StrategyModel` fields：

```python
owner_user_id: Mapped[str | None] = mapped_column(String(64), index=True)
visibility: Mapped[str] = mapped_column(String(16), default="PUBLIC")
publication_status: Mapped[str] = mapped_column(String(24), default="PUBLISHED")
```

In the same 0017 migration create `v2_strategy_publication_request` with the exact approved fields. Add FKs from `source_strategy_id` and `derived_strategy_id` to `v2_strategy.strategy_id`, and from requester/reviewer to `v2_user.user_id`. Add a PostgreSQL partial unique index:

```python
op.create_index(
    "uq_v2_publication_pending_source",
    "v2_strategy_publication_request",
    ["source_strategy_id"],
    unique=True,
    postgresql_where=sa.text("status = 'PENDING'"),
)
```

Approval updates the request to APPROVED and sets `derived_strategy_id` in the same transaction that creates the public strategy/version. Rejection sets reviewer/time/status without modifying the source private strategy.

Service methods：

```python
list_visible(user: User, market: str | None = None) -> list[Strategy]
create_private(user: User, name: str, market: str, spec: StrategySpec) -> StrategyVersion
update_private(user: User, strategy_id: str, **patch) -> Strategy
request_publication(user: User, strategy_id: str) -> StrategyPublicationRequest
approve_publication(request_id: str, actor: User) -> StrategyVersion
```

Every mutation enforces owner; non-owner gets not-found-equivalent error.

- [ ] **Step 5: 新增 owner-aware routes，收紧旧全局写接口**

Create `/my/strategies`; keep public reads. Require ADMIN/appropriate operator roles for old global register/approve/update/delete endpoints. Request bodies never accept `owner_user_id`.

- [ ] **Step 6: GREEN 与提交**

```bash
uv run pytest -q tests/test_strategy_ownership.py tests/test_strategy_manage.py tests/test_strategy_spec.py
```

Then, with authorization:

```bash
git add migrations/versions/0017_strategy_ownership.py \
  src/zhice_os_v2/infrastructure/db/models.py \
  src/zhice_os_v2/application/services/strategy_catalog_service.py \
  src/zhice_os_v2/api/routers/strategy.py src/zhice_os_v2/api/routers/my_strategies.py \
  tests/test_strategy_ownership.py
git commit -m "feat(strategies): add public and private ownership"
```

### Task 3: 强制版本不可变并支持新版本

- [ ] **Step 1: 写 RED 测试**

Create `tests/test_strategy_version_immutability.py`：

```python
def test_referenced_version_cannot_be_updated(strategy_service, referenced_version):
    with pytest.raises(ValueError, match="strategy.version_immutable"):
        strategy_service.update_spec(referenced_version.strategy_version_id, new_spec())


def test_edit_creates_next_version(strategy_service, private_strategy):
    v2 = strategy_service.create_version(private_strategy.owner, private_strategy.strategy_id, new_spec())
    assert v2.version_no == "v2"
    assert v2.strategy_version_id != private_strategy.v1.strategy_version_id
```

Run:

```bash
uv run pytest -q tests/test_strategy_version_immutability.py
```

- [ ] **Step 2: 实现引用检查和版本号分配**

Repository `is_version_referenced()` checks user instances, trade plans, positions and replays. `update_spec()` rejects PUBLISHED, ACTIVE or referenced versions. `create_version()` creates a new DRAFT `vN` without mutating prior rows.

- [ ] **Step 3: 实现 request_upgrade（仅记录升级意图，不切换）**

> **阶段边界（执行时发现并修正）**：完整的「平仓后自动升级」状态机需要按实例判断未平仓持仓，而 Phase A 的 `v2_position` 只有 `strategy_version_id`、尚无 `user_strategy_id/account_id`（Phase B 交易链归属才引入）。同一版本被多用户运行时无法按实例区分持仓。因此 `apply_ready_upgrades()` 的平仓检测自动切换 + scheduler job **移至 Phase B**（position 具备 user_strategy 归属后实现）。Phase A 只做「记录用户升级意图」这一 position-independent 的部分；升级字段 `pending_upgrade_version_id`/`upgrade_requested_at`/`upgrade_available_version_id` 已在 0019 建好，供 Phase B 使用。

Add tests:

```python
def test_request_upgrade_records_pending_intent(instance_service, users, public_version, public_version_2):
    inst = instance_service.create(users.alice, strategy_version_id=public_version.strategy_version_id,
                                   account_mode="NEW_DEDICATED", initial_capital=Decimal("100000"))
    result = instance_service.request_upgrade(users.alice, inst.user_strategy_id, public_version_2.strategy_version_id)
    assert result.pending_upgrade_version_id == public_version_2.strategy_version_id
    assert result.upgrade_requested_at is not None
    assert result.strategy_version_id == public_version.strategy_version_id  # 未切换
```

Implementation rules (Phase A 范围)：

- `request_upgrade(user, user_strategy_id, target_version_id)`：owner-scoped（非 owner → LookupError not-found 等价）；校验 target 属于**同一 strategy**、状态 PUBLISHED/ACTIVE、且不等于当前版本；置 `pending_upgrade_version_id` + `upgrade_requested_at`；**不切换** `strategy_version_id`。
- **不**在 Phase A 实现 `apply_ready_upgrades()`/平仓检测/scheduler——移至 Phase B（需 position.user_strategy_id）。

- [ ] **Step 4: GREEN**

```bash
uv run pytest -q tests/test_strategy_version_immutability.py tests/test_user_strategy_instances.py -k "version or upgrade or immutab"
```

### Task 4: 新增持久化用户账户

- [ ] **Step 1: 写 RED owner 与数值测试**

Create `tests/test_user_accounts.py`：

```python
def test_user_lists_only_own_accounts(account_service, users):
    own = account_service.create(users.alice, market="US", account_type="SHARED_PORTFOLIO", initial_capital=100000)
    account_service.create(users.bob, market="US", account_type="SHARED_PORTFOLIO", initial_capital=200000)
    assert [a.account_id for a in account_service.list_owned(users.alice)] == [own.account_id]


def test_initial_capital_must_be_positive(account_service, users):
    with pytest.raises(ValueError, match="account.invalid_capital"):
        account_service.create(users.alice, market="CN", account_type="STRATEGY_DEDICATED", initial_capital=0)
```

- [ ] **Step 2: 创建迁移 0018（down_revision=0017）建 `v2_user_account`**

Create columns exactly: bigint `id` PK, unique varchar(64) `account_id`, varchar(64) `user_id` FK, varchar(16) `market/currency`, varchar(24) `account_type`, numeric(24,8) `initial_capital/cash_balance/reserved_cash`, varchar(16) `status`, timestamptz `created_at/updated_at`. Add checks:

```text
account_type IN ('SHARED_PORTFOLIO','STRATEGY_DEDICATED')
status IN ('ACTIVE','FROZEN','CLOSED')
initial_capital > 0
cash_balance >= 0
reserved_cash >= 0
```

Add index `(user_id,market)` and FK `user_id -> v2_user.user_id`.

- [ ] **Step 3: 实现 domain/repository/service/router**

Repository exact interface：

```python
create(account: UserAccount) -> UserAccount
list_by_user(user_id: str, market: str | None = None) -> list[UserAccount]
get_owned(account_id: str, user_id: str, *, for_update: bool = False) -> UserAccount | None
```

API body contains `market/account_type/initial_capital`, never user_id.

- [ ] **Step 4: GREEN**

```bash
uv run pytest -q tests/test_user_accounts.py tests/test_accounts.py
```

### Task 5: 新增用户策略运行实例

- [ ] **Step 1: 写 RED 测试**

Create `tests/test_user_strategy_instances.py`：

```python
def test_select_public_strategy_creates_dedicated_account(instance_service, users, public_version):
    instance = instance_service.create(
        users.alice, strategy_version_id=public_version.strategy_version_id,
        account_mode="NEW_DEDICATED", initial_capital=Decimal("100000"),
    )
    assert instance.user_id == users.alice.user_id
    assert instance.enabled is False
    assert instance.account.account_type == AccountType.STRATEGY_DEDICATED


def test_shared_account_must_belong_to_same_user(instance_service, users, bob_account, public_version):
    with pytest.raises(LookupError):
        instance_service.create(users.alice, strategy_version_id=public_version.strategy_version_id, account_id=bob_account.account_id)


def test_private_strategy_only_owner_can_run(instance_service, users, alice_private_version):
    with pytest.raises(LookupError):
        instance_service.create(users.bob, strategy_version_id=alice_private_version.strategy_version_id)
```

- [ ] **Step 2: 创建迁移 0019（down_revision=0018）建 `v2_user_strategy_instance`**

Create exact columns: bigint `id` PK; unique varchar(64) `user_strategy_id`; varchar(64) `user_id/strategy_version_id/account_id`; boolean `enabled/auto_execute`; varchar(16) `execution_mode`; numeric(24,8) nullable `capital_quota`; numeric(12,6) `max_position_ratio`; JSON `risk_parameters/strategy_parameters/notification_preferences`; nullable varchar(64) `upgrade_available_version_id/pending_upgrade_version_id`; nullable timestamptz `upgrade_requested_at`; timestamptz `created_at/updated_at`. Add FKs to user/account/all three version references, checks for execution mode and `0 < max_position_ratio <= 1`, indexes `(user_id,enabled)` and `strategy_version_id`, plus partial unique active `(account_id,strategy_version_id) WHERE enabled IS TRUE`.

- [ ] **Step 3: 实现默认通知偏好与 service API**

```python
DEFAULT_NOTIFICATION_PREFERENCES = {
    "signal": True, "plan": True, "approval": True, "risk": True,
    "order": True, "fill": True, "stop_loss": True,
    "take_profit": True, "runtime_error": True,
}
```

Service methods：

```python
create(
    user: User,
    strategy_version_id: str,
    account_mode: Literal["NEW_DEDICATED", "EXISTING_SHARED"],
    *,
    account_id: str | None = None,
    initial_capital: Decimal | None = None,
    execution_mode: str = "MANUAL",
    auto_execute: bool = False,
    capital_quota: Decimal | None = None,
    max_position_ratio: Decimal = Decimal("0.1"),
    risk_parameters: dict | None = None,
    strategy_parameters: dict | None = None,
    notification_preferences: dict | None = None,
) -> UserStrategyInstance
enable(user: User, user_strategy_id: str) -> UserStrategyInstance
disable(user, user_strategy_id) -> UserStrategyInstance
update_owned(user, user_strategy_id, patch) -> UserStrategyInstance
list_owned(user, market=None, enabled=None) -> list[UserStrategyInstance]
list_enabled_for_market(market: str) -> list[UserStrategyInstance]
```

- [ ] **Step 4: 实现 `/my/strategy-instances`，运行 GREEN**

```bash
uv run pytest -q tests/test_user_strategy_instances.py tests/test_strategy_ownership.py
```

### Task 6: 持久化管理员全局访问审计

- [ ] **Step 1: 写审计 RED 测试**

Create `tests/test_admin_access_audit.py`：

```python
def test_admin_global_read_writes_audit(client, admin_headers, audit_repo):
    response = client.get("/api/v1/admin/strategy-instances", headers=admin_headers)
    assert response.status_code == 200
    audit = audit_repo.latest()
    assert audit.actor_user_id == "u-admin"
    assert audit.action == "LIST_GLOBAL"
    assert audit.resource_type == "user_strategy_instance"


def test_audit_does_not_store_sensitive_body(audit_repo):
    audit = audit_repo.latest()
    serialized = audit.to_dict()
    assert "token" not in serialized
    assert "password" not in serialized
    assert "response_body" not in serialized
```

- [ ] **Step 2: 创建迁移 0020（down_revision=0019）建 `v2_access_audit`**

Fields: audit_id unique, actor_user_id FK, action, resource_type, resource_id nullable, request_id, created_at. Index `(actor_user_id,created_at)` and `(resource_type,resource_id)`. Do not add arbitrary JSON request/response bodies.

- [ ] **Step 3: 实现 audit repository/dependency**

Admin global routes call one helper with actor/action/resource/request-id in the same request lifecycle. Audit failures fail closed for sensitive mutations and may fail open with ERROR log for read-only global dashboards; encode this policy in tests.

- [ ] **Step 4: GREEN**

```bash
uv run pytest -q tests/test_admin_access_audit.py tests/test_user_data_isolation.py
```

### Task 7: 新增 legacy/shadow/enforced 配置门并总验证

- [ ] **Step 1: 写配置 RED 测试**

```python
def test_default_execution_mode_is_legacy():
    assert AppSettings().multi_user_execution_mode == "legacy"
```

- [ ] **Step 2: 实现配置**

```python
multi_user_execution_mode: Literal["legacy", "shadow", "enforced"] = "legacy"
```

Expose `MULTI_USER_EXECUTION_MODE=${MULTI_USER_EXECUTION_MODE:-legacy}` to API/scheduler compose services and env examples.

- [ ] **Step 3: 注册 routers**

Register `my_accounts`, `my_strategies`, `my_strategy_instances` in `api/main.py`.

- [ ] **Step 4: 迁移与全量验证**

```bash
uv run alembic upgrade head
uv run alembic current
uv run pytest -q
uv run ruff check src tests
uv run mypy src/zhice_os_v2
uv run alembic heads
git diff --check
```

Expected: all exit 0；single head `0020`；legacy mode scheduler/workflow behavior unchanged。
