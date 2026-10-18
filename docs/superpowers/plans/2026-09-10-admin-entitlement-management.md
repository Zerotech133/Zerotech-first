# 管理员账户权限与权益管理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 ADMIN 完整、可靠、可审计地管理账户 RBAC 角色、会员权益和策略订阅（含永久/撤销/批量），角色变更立即吊销旧会话，支付成功自动发放且永不改角色、失败可自动重试与人工兜底。

**Architecture:** 严格按已确认规格分三个可独立发布单元实施——单元 A 会话版本与统一身份解析（迁移 0029）；单元 B 权益数据模型、原子事务、审计、撤销交易边界与前端（迁移 0030）；单元 C 支付发放状态机与补偿（迁移 0031）。领域层唯一计算实时权益状态；管理员写操作走单事务仓储（幂等 operation + 逐资源 audit）；支付以 order_id 为幂等来源。

**Tech Stack:** Python 3.11、FastAPI、Pydantic 2、SQLAlchemy 2、Alembic、pytest（SQLite 快测 + PostgreSQL 并发）、React 19、TypeScript strict、Ant Design 6、i18next、Vitest、Playwright。

**规格来源：** `docs/superpowers/specs/2026-09-09-admin-entitlement-management-design.md`（提交 48e52c4）。

---

## 全局约定

- 每个单元自成提交序列与门禁，A → B → C 顺序实施；下一个单元开始前，上一个单元的全量门禁必须绿。
- 迁移编号：A=`0029`，B=`0030`，C=`0031`，均在当前 `0028` 之上单头递增。
- 命令前缀统一 `cd /Users/join/Desktop/zhice/zhice-os-v2`；后端用 `PYTHONPATH=src .venv/bin/python -m pytest`；前端用 `npm --prefix frontend test`。
- 真实 PostgreSQL 测试标 `@pytest.mark.real_pg`，用独立测试库，`REAL_PG=1` 显式运行；`pyproject.toml` 默认 `-m 'not real_pg and not e2e'`。
- 每个提交结尾附 `Co-Authored-By: Claude <noreply@anthropic.com>`。
- 未经用户另行明确授权，全程只在本地实现与验证，不修改生产账户、不部署到 207.56.221.140。

---

# 单元 A：会话版本与统一身份解析

**目标：** `v2_user.auth_version` 作为会话吊销代数；角色/停用/改密立即失效旧会话；端点依赖与全局 VIEWER/USER 写限制统一用数据库当前身份授权。

## 目标签名（实施时严格采用）

```python
# token_service.py
def create_token(user_id, username, role, auth_version=1, *, secret=_SECRET_KEY, expires_in=3600) -> str: ...

# domain/security/user.py  User dataclass 增加
auth_version: int = 1

# api/dependencies.py
def resolve_token_identity(token: str) -> User: ...
def resolve_request_identity(request: Request, authorization: str | None) -> User: ...
def get_current_user(request: Request, authorization: str | None = Header(default=None)) -> User: ...

# infrastructure/repositories/user_repository.py
def update_role_protected(self, user_id, role, expected_auth_version) -> orm.UserModel: ...
def deactivate_protected(self, user_id, expected_auth_version) -> orm.UserModel: ...
def update_password(self, user_id, password_hash) -> orm.UserModel: ...  # 内部递增 auth_version
def _mutate_access_protected(self, user_id, expected_auth_version, *, new_role=None, deactivate=False) -> orm.UserModel: ...

# api/routers/auth.py
class UpdateRoleRequest(BaseModel):
    role: Role
    auth_version: int = Field(ge=1)
class DeactivateUserRequest(BaseModel):
    auth_version: int = Field(ge=1)
```

保留 HTTP 路径：`PUT /api/v1/auth/users/{id}/role`、`DELETE /api/v1/auth/users/{id}`（JSON body `{"auth_version": n}`）、`PUT /api/v1/auth/users/{id}/password`。

### Task A1：迁移 0029 + 模型默认值

**Files:** Create `migrations/versions/0029_user_auth_version.py`、`tests/test_migration_0029.py`；Modify `src/zhice_os_v2/infrastructure/db/models.py`（UserModel）、`src/zhice_os_v2/domain/security/user.py`、`tests/test_user_role_orm.py`。

- [ ] **A1.1 写迁移与 ORM RED**

```python
# tests/test_migration_0029.py 关键断言
def test_revision_is_0029_on_top_of_0028():
    assert MIG.revision == "0029"
    assert MIG.down_revision == "0028"

def test_upgrade_adds_non_null_auth_version_and_backfills(monkeypatch):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE v2_user (id INTEGER PRIMARY KEY, user_id VARCHAR(64) NOT NULL)")
        conn.exec_driver_sql("INSERT INTO v2_user (id, user_id) VALUES (1, 'legacy-user')")
        context = MigrationContext.configure(conn)
        monkeypatch.setattr(MIG, "op", Operations(context))
        MIG.upgrade()
        cols = {c["name"]: c for c in inspect(conn).get_columns("v2_user")}
        assert cols["auth_version"]["nullable"] is False
        assert conn.exec_driver_sql("SELECT auth_version FROM v2_user WHERE id=1").scalar_one() == 1

def test_downgrade_is_explicitly_blocked():
    with pytest.raises(RuntimeError, match="forward-only"):
        MIG.downgrade()
```

`tests/test_user_role_orm.py` 追加 `test_user_auth_version_defaults_to_one`。

- [ ] **A1.2 确认 RED**

`PYTHONPATH=src .venv/bin/python -m pytest tests/test_migration_0029.py tests/test_user_role_orm.py -q` → 因文件/字段不存在 FAIL。

- [ ] **A1.3 实现迁移与模型**

```python
revision = "0029"; down_revision = "0028"
def upgrade():
    op.add_column("v2_user", sa.Column("auth_version", sa.Integer(), nullable=False, server_default=sa.text("1")))
def downgrade():
    raise RuntimeError("0029 auth_version is forward-only; keep the column and apply a forward fix")
```

UserModel 增加 `auth_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))`；User dataclass 增加 `auth_version: int = 1`。

- [ ] **A1.4 确认 GREEN + 单头**

同 A1.2 命令 PASS；`.venv/bin/alembic heads` 只输出 `0029 (head)`。

- [ ] **A1.5 提交**

```bash
git add migrations/versions/0029_user_auth_version.py src/zhice_os_v2/infrastructure/db/models.py src/zhice_os_v2/domain/security/user.py tests/test_migration_0029.py tests/test_user_role_orm.py
git commit -m "feat(db): add user authentication version" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

### Task A2：Token claim + 登录签发 + 旧格式兼容

**Files:** Create `tests/test_auth_session_version.py`；Modify `token_service.py`、`auth_service.py`（_row 映射）、`api/routers/auth.py`（登录/注册签发）、`api/dependencies.py`、`tests/test_change_password.py`、中英文 `errors.json`。

- [ ] **A2.1 写 claim/签发/兼容矩阵 RED** — 覆盖：`create_token` 带整数 `auth_version`；登录签发读数据库当前版本；注册即登录带版本 1；解析矩阵 8 例（版本匹配放行、不匹配 401 `auth.session_revoked`、缺 claim 且 DB 版本=1 放行、缺 claim 且 DB 版本>1 拒绝、Token 旧角色→用 DB 当前角色、inactive→401、`password_changed_at` 校验仍生效、无 iat 旧 token 改密后仍失效）。构造旧 token 用测试内本地 `_sign_payload()`，不给生产加测试专用签发。
- [ ] **A2.2 确认 RED** — `pytest tests/test_auth_session_version.py tests/test_change_password.py -q` FAIL；既有 `password_changed_at` 测试保持当前结果。
- [ ] **A2.3 实现** — payload 增 `auth_version`；`issued_at = int(time.time())` 单次读取；`AuthService._row()` 映射 `auth_version`；登录/注册两签发点显式传 `user.auth_version`。`resolve_token_identity` 严格顺序：解码→user_id→(非 sql 用 token 构造)→查库存在且 active→版本谓词→保留 `password_changed_at`/iat 校验→返回全部字段取自 row 的 User。兼容谓词：

```python
claim = token_data.get("auth_version")
version_ok = (row.auth_version == 1) if claim is None else (type(claim) is int and claim == row.auth_version)
```

`errors.json` 中英文加 `auth.session_revoked`、`auth.auth_version_conflict`。

- [ ] **A2.4 确认 GREEN + 回归** — `pytest tests/test_auth_session_version.py tests/test_auth.py tests/test_auth_register.py tests/test_change_password.py tests/test_user_management.py -q` 全绿。
- [ ] **A2.5 提交** — `feat(auth): validate session version during login`。

### Task A3：所有安全变更原子递增版本 + 乐观锁

**Files:** Modify `user_repository.py`、`auth_service.py`、`api/routers/auth.py`、`tests/test_user_management.py`、`tests/test_change_password.py`、`frontend/src/pages/UsersPage.tsx`、`frontend/src/test/UserManagementSecurity.test.tsx`。

- [ ] **A3.1 写四类递增 RED** — 仓储/API 覆盖：角色变更版本+1；旧版本冲突不改动；停用版本+1；本人改密版本+1；管理员重置目标版本+1（管理员本人不变）；旧密码错不递增；最后管理员拒绝不递增。
- [ ] **A3.2 写 API 乐观锁 RED** — 角色/停用请求带 `auth_version`；冲突返回 409 `{"code":"auth.auth_version_conflict"}`；`GET /auth/users`、登录、`/auth/me` 均返回当前 `auth_version`。
- [ ] **A3.3 确认 RED** — `pytest tests/test_user_management.py tests/test_change_password.py tests/test_auth_session_version.py -q` FAIL。
- [ ] **A3.4 实现固定锁序事务模板** — `_mutate_access_protected`：先按 `id ASC` `with_for_update` 锁全部 active ADMIN，再 `with_for_update` 锁 target；校验存在→版本→最后管理员→改字段→`auth_version += 1`→commit；异常 rollback。删除公开 `update_role()`/`deactivate()`。改密事务同时更新 `password_hash`、`password_changed_at`、`auth_version += 1`。
- [ ] **A3.5 状态码** — 冲突/最后管理员 409，not_found 404，非法模型 422，成功回最新 `auth_version`。本单元不加 reason/Idempotency-Key（B 接入）。
- [ ] **A3.6 前端最小兼容** — `UserRow` 加 `auth_version`；角色/停用请求带 `row.auth_version`；Vitest 断言携带服务端版本。
- [ ] **A3.7 确认 GREEN** — 后端三测 + `npm --prefix frontend test -- --run src/test/UserManagementSecurity.test.tsx`；`grep -R -nE '\.update_role\(|\.deactivate\(' src/zhice_os_v2 --exclude-dir=__pycache__` 无仓储无保护调用。
- [ ] **A3.8 提交** — `feat(auth): revoke sessions on account security changes`。

### Task A4：全局中间件统一用数据库身份

**Files:** Modify `api/dependencies.py`、`api/main.py`、`tests/test_rbac_viewer.py`、`tests/test_auth_session_version.py`。

- [ ] **A4.1 改写旧中间件测试为 DB 实时角色 RED** — 矩阵：Token=ADMIN/DB=VIEWER→写 403 viewer_readonly；Token=ADMIN/DB=USER→非 owner 路径 403；Token=VIEWER/DB=ADMIN→放行；Token=USER/DB=ADMIN→放行；版本过期 Token=ADMIN→401 而非旧角色放行。
- [ ] **A4.2 写单请求只读一次身份 RED** — 包装 `UserRepository.get_by_id` 计数，选一个既过写限制又进 `Depends(get_current_user)` 的写端点，断言 `calls == 1`。
- [ ] **A4.3 确认 RED**。
- [ ] **A4.4 实现 request-state 缓存** — `resolve_request_identity` 缓存到 `request.state.authenticated_user`；`get_current_user` 只调它。
- [ ] **A4.5 合并两中间件** — 删除 `viewer_readonly`/`user_owner_scope_only` 中 `decode_token`/`data.get("role")` 授权；合并为 `database_role_write_guard`：非写方法放行；VIEWER 白名单路径放行；有 Authorization 则 `resolve_request_identity` 后按 DB 角色判 VIEWER/USER；无 Authorization 下传；无效 token 在判角色前 401。`/api/v1/my/*`、`/api/v1/billing/orders` 对 USER 放行、VIEWER 拒绝。
- [ ] **A4.6 确认 GREEN** — 四测全绿；`grep -R -nE 'token_data.*role|data\.get\("role"\)' src/zhice_os_v2/api --exclude-dir=__pycache__` 无授权分支读 token role。
- [ ] **A4.7 提交** — `fix(auth): unify write guards with database identity`。

### Task A5：PostgreSQL 并发验证最后管理员锁序

**Files:** Create `tests/test_auth_admin_concurrency_pg.py`。

- [ ] **A5.1 SQLite 锁序结构测试** — `before_cursor_execute` 断言首条管理员 SELECT 含 `role='ADMIN'` 且 `ORDER BY v2_user.id ASC`，发生在 target 查询前。不得声称"证明无死锁"。
- [ ] **A5.2 PG 双线程 RED** — 整文件 `pytestmark = pytest.mark.real_pg`；要求 `REAL_PG=1` 且 `DB_MAIN_URL` 指向名含 test 的库，否则拒跑；`@pytest.mark.parametrize` 四组（demote/deactivate 交叉），`threading.Barrier(2)`；断言无线程存活、成功 1、失败 1 `auth.last_admin_protected`、`count_admins()==1`。
- [ ] **A5.3 建测试库迁到 0029 并确认 RED**

```bash
docker compose up -d postgres
docker compose exec -T postgres dropdb --if-exists -U zhice zhice_os_v2_unit_a_test
docker compose exec -T postgres createdb -U zhice zhice_os_v2_unit_a_test
DB_MAIN_URL=postgresql://zhice:zhice_pass@localhost:5432/zhice_os_v2_unit_a_test .venv/bin/alembic upgrade 0029
REAL_PG=1 DB_MAIN_URL=postgresql://zhice:zhice_pass@localhost:5432/zhice_os_v2_unit_a_test PYTHONPATH=src .venv/bin/python -m pytest -o addopts="" -m real_pg tests/test_auth_admin_concurrency_pg.py -q
```

- [ ] **A5.4 GREEN + 20 次稳定性** — 修正后连续 20 次全绿，无超时/DeadlockDetected/零管理员/双成功；`dropdb` 清理。
- [ ] **A5.5 提交** — `test(auth): verify PostgreSQL administrator lock ordering`。

### Task A6：迁移实演 + 全量门禁 + 发布手册

**Files:** Create/Append `docs/runbooks/admin-entitlement-rollout.md`。

- [ ] **A6.1 全新 PG 迁移实演** — 建库→`upgrade 0028`→插旧行→`upgrade 0029`→验证 `auth_version` NOT NULL/default 1/旧行回填 1；不执行成功 downgrade。
- [ ] **A6.2 单元 A 专项门禁** — `pytest tests/test_migration_0029.py tests/test_auth_session_version.py tests/test_user_management.py tests/test_change_password.py tests/test_rbac_viewer.py tests/test_user_role_orm.py -q` + 前端安全测试。
- [ ] **A6.3 全量门禁** — `pytest -q`、`ruff check src tests`、`mypy src`（无新增文件相关错误）、`npm --prefix frontend test -- --run`、`npm --prefix frontend run build`。
- [ ] **A6.4 维护窗口发布实演** — 停 api/scheduler→迁移→起 api/scheduler；断言同镜像；smoke：旧版本 1 token 仍可用、新登录带版本 1、改角色后旧 token 401、新登录用 DB 当前角色、DB 角色改 VIEWER/USER 后写限制即时生效。禁止新旧二进制混跑。
- [ ] **A6.5 提交** — `docs(auth): document unit A rollout gate`。

---

# 单元 B：管理员权益管理、审计、撤销交易边界与前端

**目标：** 迁移 0030 引入永久/撤销/乐观锁字段、幂等 operation、逐资源 audit；统一实时权益谓词；管理员查询/设置/撤销/批量 API；会员撤销停止新开仓风险；用户管理抽屉与双语。

## 分部说明

- **B-后端核心（B1）**：迁移、领域谓词、operation/audit 仓储、幂等指纹、资格谓词、原子事务服务、查询/写 API、旧 grant 接入。
- **B-撤销交易边界（B2）**：会员撤销事务的交易工作取消与开仓提交门禁。**注意：撤销会员的公开 API 端点必须在 B2 完成后才注册**——B1 只实现 `revoke_membership_core`（权益+审计+幂等），不挂公开 revoke 路由。
- **B-前端（B3）**：USERS 页与抽屉、双语、E2E。B3 开始前先按 Gate 0 核对 B1/B2 实际 OpenAPI 契约。

## B1 目标签名

```python
# domain/billing/entitlement.py
EffectiveStatus = Literal["free","active","permanent","expired","revoked"]
def membership_effective_status(m, now=None) -> EffectiveStatus: ...
def subscription_effective_status(s, now=None) -> EffectiveStatus: ...
def is_member_tier_active(m, now=None) -> bool: ...
def is_subscription_active(s, now=None) -> bool: ...
def missing_membership(user_id) -> Membership: ...   # version=0
def membership_view(m, *, user_id, now=None) -> dict: ...
def subscription_view(s, *, now=None) -> dict: ...

# domain/strategy/eligibility.py
def deployment_enabled_markets(raw, *, supported=SUPPORTED_MARKETS) -> frozenset[str]: ...
def is_grantable_platform_strategy(strategy, *, enabled_markets) -> bool: ...

# application/services/admin_operation_fingerprint.py
def normalize_idempotency_key(raw) -> str: ...
def request_fingerprint(*, method, route, action, target_user_id, body) -> str: ...

# application/services/admin_entitlement_service.py
class AdminEntitlementService:
    def set_membership(self, *, context, target_user_id, mode, valid_until, reason, expected_version) -> AdminWriteResult: ...
    def revoke_membership_core(self, *, context, target_user_id, reason, expected_version) -> AdminWriteResult: ...
    def set_subscriptions(self, *, context, target_user_id, strategy_ids, mode, valid_until, reason, expected_versions) -> AdminWriteResult: ...
    def revoke_subscriptions(self, *, context, target_user_id, strategy_ids, reason, expected_versions) -> AdminWriteResult: ...
    def list_users(self, *, page, page_size, query) -> dict: ...
    def get_user_detail(self, user_id) -> dict: ...
    def list_audits(self, query: AuditQuery) -> dict: ...
```

### Task B1-1：迁移 0030（preflight/约束/受限 downgrade）

**Files:** Create `migrations/versions/0030_admin_entitlements.py`、`tests/test_migration_0030.py`；Modify `models.py`。

- [ ] **RED** — 断言 revision 0030/down 0029；`_assert_upgrade_safe` 在 `member_without_valid_until`/`free_with_valid_until`/未知状态时抛 RuntimeError 且在任何 DDL 前；`_assert_downgrade_safe` 在存在永久/撤销记录时抛错；`uq_v2_admin_operation_actor_key` 唯一。
- [ ] **实现 schema** — `v2_membership`/`v2_strategy_subscription` 加 `is_permanent BOOLEAN NOT NULL DEFAULT false`、`revoked_at TIMESTAMPTZ NULL`、`revoked_by_user_id VARCHAR(64) NULL`、`version INTEGER NOT NULL DEFAULT 1`；订阅 `valid_until` 改可空；`status` 约束加 `revoked`；建 CHECK（永久/日期互斥、撤销字段成对、`status=revoked ⇔ revoked_at NOT NULL`、`version>=1`、membership free/member shape）。建 `v2_admin_operation`（`operation_id` PK、`UNIQUE(actor_user_id, idempotency_key)`、JSONB/JSON 变体）、`v2_entitlement_audit`（`source_order_id` 唯一多 NULL、索引 `(target_user_id,created_at)`/`(actor_user_id,created_at)`/`(resource_type,resource_id,created_at)`/`(action,created_at)`/`operation_id`）。
- [ ] **GREEN + PG rehearsal** — `pytest tests/test_migration_0030.py -q`；隔离 PG 库 `upgrade 0030 → downgrade 0029 → upgrade 0030`。
- [ ] **提交** — `feat(entitlements): add admin entitlement schema`。

### Task B1-2：统一实时权益语义

**Files:** Modify `domain/billing/entitlement.py`、`membership_repository.py`、`membership_service.py`；Create `tests/test_entitlement_domain.py`；Modify `tests/test_billing_membership.py`、`tests/test_billing_data_gating.py`。

- [ ] **RED** — 状态矩阵参数化（缺失/free/permanent/revoked/active/expired）；缺失投影 version 0；`stored_status=active` 不覆盖到期判定。
- [ ] **实现** — 会员优先级：缺失/free→free；revoked；permanent；`valid_until>=now`→active；否则 expired。订阅同构（无 free）。`list_active_subscriber_ids` SQL 改 `revoked_at IS NULL AND (is_permanent OR valid_until>=:now)`；`list_expired_member_ids` 排除 permanent/revoked。
- [ ] **GREEN** — `pytest tests/test_entitlement_domain.py tests/test_billing_membership.py tests/test_billing_data_gating.py -q`。
- [ ] **提交** — `feat(entitlements): define effective entitlement states`。

### Task B1-3：operation/audit 领域与仓储

**Files:** Create `domain/billing/admin_entitlement.py`、`infrastructure/repositories/admin_entitlement_repository.py`、`tests/test_admin_entitlement_repository.py`。

- [ ] **RED/实现/GREEN** — `AdminOperation`/`EntitlementAudit`/`AdminWriteContext`/`AdminWriteResult`/`AuditQuery` dataclass；`get_operation`、`list_audits`（`created_at DESC, audit_id DESC`）、`recent_audits(limit=20)`、`count_*`。`strategy_id` 过滤映射 `resource_id`；每过滤器一测；`from/to` 闭区间；PAYMENT 同 `source_order_id` 不可重复；audit 不含 token/密码/栈/webhook。
- [ ] **提交** — `feat(entitlements): add operation and audit repository`。

### Task B1-4：幂等 key 与请求指纹

**Files:** Create `application/services/admin_operation_fingerprint.py`、`tests/test_admin_operation_fingerprint.py`；Modify `tests/test_architecture.py`（若需）。

- [ ] **RED/实现/GREEN** — key trim 后 8~128 可打印 ASCII；指纹规范化：method 大写、route 模板、reason trim、strategy_ids 去重排序、versions 按键排序、datetime→UTC 固定格式、permanent 删 valid_until、稳定 JSON `sort_keys`+`separators=(",",":")`→SHA-256（64 hex）。测试：顺序/重复/时区不变指纹相同；非法 key 抛错；method/route/target/version 参与指纹。
- [ ] **提交** — `feat(entitlements): canonicalize admin idempotency requests`。

### Task B1-5：统一平台策略资格谓词

**Files:** Create `domain/strategy/eligibility.py`、`tests/test_platform_strategy_eligibility.py`；Modify `strategy_catalog_service.py`、`payment_service.py`、`api/routers/billing.py`、`tests/test_billing_payment.py`。

- [ ] **RED/实现/GREEN** — 谓词 `owner_user_id is None AND visibility=='PUBLIC' AND publication_status=='PUBLISHED' AND status==ACTIVE AND market in enabled_markets`。替换 `payment_service._assert_builtin_strategy` 为 `_assert_grantable_platform_strategy`（保留错误码 `billing.strategy_not_found`；新增 `billing.strategy_not_grantable`）。新增只读 `GET /api/v1/billing/strategies`（按 market,strategy_id 排序）。矩阵测试六行；catalog/支付/管理员三处同结论。
- [ ] **提交** — `fix(billing): unify grantable platform strategy policy`。

### Task B1-6：管理员权益原子事务与乐观锁

**Files:** Create `application/services/admin_entitlement_service.py`；Modify `admin_entitlement_repository.py`、`tests/test_admin_entitlement_transactions.py`、`tests/test_architecture.py`。

- [ ] **RED/实现/GREEN** — 事务顺序：幂等 operation 查 →（同 key 同指纹 replay / 不同指纹 409）→锁 target user→锁 membership→按 strategy_id 排序锁 subscriptions→改字段→写逐资源 audit→写 completed operation→commit。规则：set_membership（inactive 拒、until 需带时区且晚于 now、permanent 无日期、缺行 version 0）、revoke_membership_core（缺行/free→`membership_not_granted`、已撤销同版本 no-op 不递增）、set_subscriptions（去重 1~100、versions 键完全一致、每项资格谓词、任一失败整批回滚、一 operation + 每策略一 audit）、revoke_subscriptions（缺失→`subscription_not_granted`、不改会员）。并发首次插入命中 `uq_v2_admin_operation_actor_key` 只捕获该约束。
- [ ] **提交** — `feat(entitlements): add atomic admin entitlement writes`。

### Task B1-7：查询/详情/审计分页 API

**Files:** Create `api/routers/admin_entitlements.py`、`api/schemas/admin_entitlements.py`；Modify `api/main.py`（注册 `admin_entitlements.router`，紧邻 `admin_billing`）、`admin_entitlement_repository.py`、`tests/test_admin_entitlement_queries_api.py`。

- [ ] **RED/实现/GREEN** — `GET /admin/entitlements/users`（page/page_size≤100/query，`created_at DESC,user_id DESC`，会员实时状态、有效订阅计数用谓词非 `status='active'`）、`GET /admin/entitlements/users/{id}`（缺失会员 free/version 0，订阅含 active/permanent/expired/revoked 按 strategy_id 排序，recent_audits≤20，`payment_grant_summary` C 前用旧口径 `status='paid' AND granted_at IS NULL` 统计 pending 其余 0）、`GET /admin/entitlements/audits`（page_size≤200，时间需带时区否则 422）。非 ADMIN 403。全部 `require_roles(Role.ADMIN)`。
- [ ] **提交** — `feat(entitlements): expose admin entitlement queries`。

### Task B1-8：设置会员/批量订阅写 API

**Files:** Modify `api/routers/admin_entitlements.py`、`api/schemas/admin_entitlements.py`；Create `tests/test_admin_entitlement_writes_api.py`。

- [ ] **RED/实现/GREEN** — `PUT .../membership`、`PUT .../subscriptions`、`POST .../subscriptions/revoke`。请求含 `Idempotency-Key` header（`get_idempotency_key` 依赖）、`reason`、`version`/`versions`。错误码映射：user_not_found 404、strategy_not_grantable 400、version_conflict/idempotency_conflict/user_inactive/subscription_not_granted 409、invalid_reason/invalid_idempotency_key/invalid_expiry/invalid_batch/versions_mismatch 422。响应用 `JSONResponse(status_code=result.status_code, content=result.body)` 精确 replay。**本任务不注册 membership/revoke 公开端点**（等 B2）。
- [ ] **提交** — `feat(entitlements): add admin membership and subscription writes`。

### Task B1-9：角色修改与旧 grant 接入同一事务

**Files:** Modify `api/routers/auth.py`、`auth_service.py`、`user_repository.py`、`api/routers/admin_billing.py`、`admin_entitlement_service.py`、`admin_entitlement_repository.py`、`tests/test_admin_entitlement_legacy_adapters.py`（新建）、`tests/test_user_management.py`。

- [ ] **RED/实现/GREEN** — `UpdateRoleRequest` 加 `reason`；新增 `change_role`（复用 A 的锁序 + ROLE audit + operation + `auth_version+1`）。旧 `grant-membership`/`grant-subscription` body 加 `reason`+`version`，要求 `Idempotency-Key`，内部复用新事务与资格谓词；permanent 收月费 no-op 不降级；不接受 order_id。role replay 不二次递增版本；最后管理员拒绝不写 audit/operation。
- [ ] **提交** — `refactor(entitlements): route legacy grants through audited transactions`。

### Task B1-10：PG 并发 + B1 门禁

**Files:** Create `tests/test_admin_entitlement_concurrency_pg.py`。

- [ ] **RED/GREEN** — `@pytest.mark.real_pg`：同 key 同指纹→一结果一 operation；同 key 异指纹→一成功一 idempotency_conflict；同资源异 key 同版本→一成功一 version_conflict；批量失败全回滚；两管理员降级不死锁保一 ADMIN。只识别 `uq_v2_admin_operation_actor_key`。
- [ ] **B1 全量门禁** — 专项 pytest + real_pg + `pytest -q` + `ruff check src/zhice_os_v2 tests` + `mypy src/zhice_os_v2`；`alembic current`=0030；确认无公开 `POST .../membership/revoke`。
- [ ] **提交** — `test(entitlements): verify admin write concurrency`。

## B2 撤销交易边界

**目标签名：**

```python
# infrastructure/repositories/entitlement_risk_repository.py
@dataclass(frozen=True)
class OpeningRiskCleanupResult: disabled_instance_ids; cancelled_plan_ids; cancelled_approval_ids; cancelled_intent_ids; cancelled_request_ids; released_reservation_ids; released_amount; pending_external_cancellations
class BrokerCancellationOutboxPort(Protocol): supports_durable_outbox: bool; def enqueue_in_session(self, session, *, target_user_id, user_strategy_ids, now) -> Sequence[str]: ...
class SimulatedOnlyBrokerCancellationOutbox: supports_durable_outbox = False  # 返回 ()
class EntitlementRiskRepository:
    def stop_new_opening_risk(self, session, *, actor_user_id, target_user_id, now, cancellation_outbox) -> OpeningRiskCleanupResult: ...

# application/services/opening_submission_guard.py
class OpeningSubmissionDenied(RuntimeError): code
class OpeningSubmissionGuard:
    @contextmanager
    def hold(self, *, user_strategy_id) -> Iterator[OpeningSubmissionSnapshot]: ...
```

### Task B2-1：撤权清理仓储 + 撤销事务

**Files:** Create `entitlement_risk_repository.py`、`tests/test_membership_revocation_opening_risk.py`；Modify `admin_entitlement_service.py`（`revoke_membership` 完整入口，同 session 调用清理）。

- [ ] **RED/实现/GREEN** — `stop_new_opening_risk` 不 commit/close；只停 `owner_user_id==target AND visibility=='PRIVATE'` 的 enabled 实例（enabled→false）；取消这些实例下 `PENDING/APPROVED` 计划→CANCELLED；取消 `direction=='BUY'` 且未提交的 `PENDING/APPROVED/EXECUTING` 审批；取消 `intent_type=='OPEN' AND intent_side=='BUY' AND status=='NEW'` 且无 SUBMITTED/SENT request 的 intent；`cancelled_request_ids=()`；释放 `status=='RESERVED'` 且 idempotency_key 属被取消计划集的预留（`reserved_cash -= sum`，`cash_balance` 不变，超额抛 `entitlements.reservation_balance_inconsistent`）。锁序：operation→user→membership→instances(id ASC)→accounts(account_id ASC)→plans→approvals→intents→reservations→audit。测试覆盖：只取消未提交私有入场、保留 EXECUTED/FILLED/SUBMITTED/CLOSE/SELL/持仓、audit 失败整体回滚。
- [ ] **提交** — `feat(entitlements): stop queued opening risk on revoke`。

### Task B2-2：开仓提交门禁

**Files:** Create `opening_submission_guard.py`、`tests/test_opening_submission_guard.py`。

- [ ] **RED/实现/GREEN** — `hold()` 短事务锁 user→membership→instance，解析自建策略后对 owned 实例查 `enabled` 且 `is_member_tier_active`（复用 B1 谓词，不复制），yield 期间持锁到 broker 返回。测试：revoked 拒、disabled 拒、active 放行、平台策略实例免会员。
- [ ] **提交** — `feat(execution): add locked opening submission guard`。

### Task B2-3：三个 broker 提交边界接入 + LIVE fail-closed

**Files:** Modify `user_instance_execution_service.py`、`workflow_service.py`、`approval_execution_service.py`、`execution_orchestrator.py`；Create `tests/test_opening_submission_boundaries.py`。

- [ ] **RED/实现/GREEN** — 三处 BUY 提交前包 `guard.hold`：owned pipeline `SimulatedBroker.place_order` 前、workflow `_auto_execute` claim 成功后 broker 前、orchestrator 每个 enabled broker 提交前（仅 `side==BUY and user_strategy_id`）。拒绝时不调 broker、释放 RESERVED、不建仓、退出/SELL 不受限。orchestrator 在 `switch.live_enabled` 分支前若 `not cancellation_outbox.supports_durable_outbox` 抛 `broker.external_cancellation_not_configured`。回归 `test_owned_execution_pipeline.py`、`test_auto_execute_claim_rollback.py`、`test_oversell.py`、`test_brokers.py`。
- [ ] **提交** — `feat(execution): recheck entitlements at broker boundaries`。

### Task B2-4：PG 线性化 + 注册公开撤销端点

**Files:** Create `tests/test_opening_submission_guard_pg.py`；Modify `api/routers/admin_entitlements.py`（注册 `POST .../membership/revoke`）、`tests/test_admin_entitlement_writes_api.py`。

- [ ] **RED/GREEN** — `@pytest.mark.real_pg`：撤权先提交则 guard 拒 broker；worker 先持锁则撤权等待到 broker 返回后才提交（事件顺序断言）。此时才注册公开 revoke 端点，API 测试断言返回停用实例/释放金额/`pending_external_cancellations`。
- [ ] **提交** — `test(entitlements): verify revoke and broker lock ordering`。

## B3 前端与浏览器 E2E

**Gate 0（必做）：** 实施前用 `curl -fsS http://127.0.0.1:8102/openapi.json` 核对 B1/B2 实际分页结构、字段名、错误码、`GET /admin/entitlements/strategies` 是否存在；契约不符先回补后端，不在前端重造资格谓词。

### Task B3-1：加固 API 客户端 + 幂等请求

**Files:** Modify `frontend/src/services/api.ts`；Create `frontend/src/services/adminEntitlementsApi.ts`、`frontend/src/components/admin-entitlements/entitlementModel.ts`、`frontend/src/test/adminEntitlementsApi.test.ts`。

- [ ] **RED/实现/GREEN** — 修复 `api.ts` headers 合并（不让 `init.headers` 覆盖 Authorization/Accept-Language）；管理员写函数显式接 `idempotencyKey`；permanent 不发 valid_until；until 转 UTC ISO；strategy_ids 去重排序 + versions 一致；`encodeURIComponent`；GET 接 `AbortSignal`；401 同时清 token+role；超时/网络错误 `retryable=true`，409/422 不可重试，写请求不自动重试。命令 `npx vitest run src/test/adminEntitlementsApi.test.ts` + `npm run build`。
- [ ] **提交** — `feat(admin): add typed entitlement API client`。

### Task B3-2：补双语 + 管理员用户摘要列表

**Files:** Modify `UsersPage.tsx`、中英文 `p_pages2.json`/`errors.json`；Create `frontend/src/test/UsersPageEntitlements.test.tsx`；Modify `UserManagementSecurity.test.tsx`。

- [ ] **RED/实现/GREEN** — `ALL_ROLES` 加 `USER`（顺序 ADMIN,USER,TRADER,RESEARCHER,RISK_MANAGER,VIEWER）；中文"普通用户"/英文"User"；角色列改只读 Tag；调 `/admin/entitlements/users` 分页；会员实时列 + 有效订阅数；"权限与权益"按钮；"待处理支付"按钮 disabled（tooltip 单元 C）；查询失败显示错误态不投影免费；刷新失败保留旧数据提示过期。errors.json 补 `auth.session_revoked` 与 `entitlements.*`。
- [ ] **提交** — `feat(admin): show entitlement user summaries`。

### Task B3-3：四区抽屉 + 迟到响应隔离

**Files:** Create `EntitlementDrawer.tsx`、`frontend/src/test/EntitlementDrawer.test.tsx`；Modify `UsersPage.tsx`、`p_pages2.json`。

- [ ] **RED/实现/GREEN** — 四区（角色/会员/订阅/操作记录）可按标题查询；抽屉局部 loading 不影响列表；`AbortController` + `requestEpochRef` 双保险防迟到响应；切换用户清空旧详情；stored/effective 分开展示；订阅含撤销历史；支付摘要只展示计数与占位。
- [ ] **提交** — `feat(admin): add entitlement management drawer`。

### Task B3-4：角色/会员/批量订阅写操作

**Files:** Modify `EntitlementDrawer.tsx`、`EntitlementDrawer.test.tsx`、`p_pages2.json`。

- [ ] **RED/实现/GREEN** — 三块独立提交独立 loading，无"全部保存"；角色带 `auth_version`+reason+危险降级二次确认；会员 until/permanent 互斥、free 用 version 0、撤销确认文案含"停止新开仓/停用自建实例/保留退出管理/不撤销订阅"；订阅候选来自 grantable 端点、批量去重排序、versions 从详情取（缺=0）、单项走批量撤销。inactive 用户角色可改、已有权益可撤、设置/授权 disabled。写成功先合并响应再刷新，刷新失败显示"操作已提交，状态刷新失败"只给重载入口。
- [ ] **提交** — `feat(admin): manage roles memberships and subscriptions`。

### Task B3-5：未知结果重试 + 陈旧状态 + 自降级登出

**Files:** Modify `entitlementModel.ts`、`EntitlementDrawer.tsx`、`EntitlementDrawer.test.tsx`、`api.ts`、`UsersPage.tsx`。

- [ ] **RED/实现/GREEN** — 冻结请求体+幂等键，超时后"重试原操作"复用同键同体，不自动重发、不给"换新键"；仅超时/断连/网络错为未知结果，4xx 为已知失败换新键；自降级（目标==当前 actor 且角色改非 ADMIN）清 token+role 跳 /login。UsersPage 并行读 `/auth/me` 取 actor id。
- [ ] **提交** — `fix(admin): make entitlement writes retry safe`。

### Task B3-6：自助页显示永久权益

**Files:** Modify `billingApi.ts`、`BillingPage.tsx`；Create/Modify `frontend/src/test/BillingPage.test.tsx`、`p_pages2.json`。

- [ ] **RED/实现/GREEN** — `MembershipView`/`SubscriptionView` 加 `is_permanent`+`effective_status`（以 B1 实际自助响应为准）；permanent 显示"永久有效"非 `—`；expired/revoked 不显示 active；不改支付轮询状态机。
- [ ] **提交** — `feat(billing): display permanent entitlements`。

### Task B3-7：Playwright E2E

**Files:** Modify `frontend/package.json`；Create `frontend/playwright.config.ts`、`frontend/e2e/admin-entitlements.spec.ts`。

- [ ] **安装与配置** — `npm i -D @playwright/test`；`playwright install chromium`；`workers:1`、`retries:0`、trace/screenshot/video retain-on-failure。
- [ ] **真实旅程（serial）** — 普通用户无入口/403；admin 看 USER 双语摘要与只读角色；抽屉四区；改在线目标 TRADER→USER 后其旧会话跳登录；设永久会员；批量授权两策略；目标重登看到永久权益与两订阅；撤销会员后新开仓停、自建实例 disabled、订阅仍在；操作记录含 actor/前后值/原因/时间/一 operation 两 audit；第二管理员改自身角色后立即登出。
- [ ] **故障注入** — route mock 陈旧响应、幂等重试同键同体、独立 loading、成功后刷新失败只给重载。
- [ ] **提交** — `test(e2e): cover admin entitlement management`。

**B 最终门禁：** 前端专项 Vitest + `npm --prefix frontend test` + `npm --prefix frontend run build`；后端 `pytest -q` + real_pg 专项 + `ruff check` + `mypy`；`git diff --check` 无输出；`alembic current`=0030。

---

# 单元 C：支付发放状态机与补偿

**目标：** 迁移 0031 引入发放状态/租约/历史歧义复核/产品快照/策略删除 RESTRICT；订单级幂等原子发放；短认领租约与崩溃回收；自动 8 次退避 + 人工兜底；inactive 履约、permanent 不降级；自助页与管理员支付管理 API。

## 现状锚点（已核对）

- `domain/billing/payment.py:9-26` `PaymentOrder` dataclass（status: pending|paid|expired|canceled）。
- `payment_repository.py`：`create_order`、`attach_gateway`、`process_payment`（行锁+trade_id 唯一幂等，返回 processed/duplicate/not_found/bad_state）、`mark_granted`、`expire_stale`。
- `payment_service.py:187-234` `handle_notify`：`process_payment` 后发放 grant + `mark_granted`，失败仅告警+抛异常（**这是要修的可靠性缺口**）。
- `scheduler.py:197-202` `expire_payments` job 已注册；`scheduler_jobs.py:305-319` `expire_payments_job`。
- `strategy_service.py:242-276` `_purge_strategy_references` 会 `DELETE FROM v2_strategy_subscription`——需改为对有支付历史的策略 RESTRICT。
- `billing.py`/`billing_webhook.py`、`main.py:242-244` 路由注册。
- `tests/test_billing_payment.py` 隔离 SQLite fixture `pay_env` + `_FakeClient`。

## C 目标签名

```python
# domain/billing/payment.py  PaymentOrder 增字段
grant_status: str = "pending"  # pending|processing|retrying|granted|failed|manual_review
grant_attempts: int = 0
automatic_grant_attempts: int = 0
next_grant_retry_at: datetime | None = None
last_grant_error_code: str | None = None
last_grant_error_summary: str | None = None
grant_lease_until: datetime | None = None
grant_lease_owner: str | None = None
historical_grant_ambiguous: bool = False
grant_reviewed_at: datetime | None = None
grant_reviewed_by_user_id: str | None = None
grant_review_decision: str | None = None  # RETRY|MARK_GRANTED
product_name_snapshot: str = ""
market_snapshot: str | None = None

# payment_repository.py 新方法
def claim_next_grant(self, *, now, lease_owner, lease_seconds=300) -> PaymentOrder | None: ...   # FOR UPDATE SKIP LOCKED
def apply_grant_atomic(self, *, order_id, lease_owner, grant_fn) -> str: ...  # order→user→entitlement→audit→granted 同事务
def mark_grant_failure(self, *, order_id, lease_owner, error_code, error_summary, next_retry_at, reached_max) -> None: ...
def list_grant_queue(self, *, page, page_size, grant_status=None, user_id=None) -> tuple[list, int]: ...
def review_historical(self, *, order_id, decision, reviewer_user_id, reason) -> str: ...

# application/services/payment_grant_service.py
class PaymentGrantService:
    def grant_order(self, order: PaymentOrder) -> None: ...   # 幂等，按 product_type 只发对应权益
    def process_due_grants(self, *, lease_owner, limit=20) -> dict: ...
    def retry_manual(self, *, order_id, actor_user_id, reason) -> AdminWriteResult: ...
```

### Task C-1：迁移 0031（preflight/历史/快照/RESTRICT）

**Files:** Create `migrations/versions/0031_payment_grant_status.py`、`tests/test_migration_0031.py`；Modify `models.py`（PaymentOrderModel）。

- [ ] **RED** — revision 0031/down 0030；preflight 检查历史 subscription 订单无孤儿 `product_ref`（有则抛错列 ID）；历史回填规则测试。
- [ ] **实现 schema** — 加上述 grant_* / snapshot 字段；`product_ref` 加对 `v2_strategy.strategy_id` 的可空 FK（非级联）；CHECK：membership `product_ref IS NULL`、subscription `product_ref IS NOT NULL`、attempts 非负、retry/review 字段组合；回填：`granted_at IS NOT NULL`→granted；历史 `status=paid AND granted_at IS NULL`→`manual_review`+`historical_grant_ambiguous=true`+`next_grant_retry_at=NULL`；`product_name_snapshot`/`market_snapshot` 从当前策略/订单回填，无法确定则阻止迁移。
- [ ] **GREEN + PG rehearsal**。
- [ ] **提交** — `feat(billing): add payment grant status machine schema`。

### Task C-2：策略删除对有支付历史 RESTRICT

**Files:** Modify `strategy_service.py`（delete 前置检查 + `_purge_strategy_references` 不再删有 pending/paid 未发放订单的订阅）；Create/Modify `tests/test_strategy_delete_publication_cleanup.py`。

- [ ] **RED/实现/GREEN** — 策略存在 pending 或 paid-未 granted 订单时，`delete()` 抛 `PermissionError("strategy.has_payment_orders", [...])`（409 引导）；已 granted 历史订单允许删策略但保留订单快照展示（订单 FK RESTRICT，故无未决订单时才删；有订阅行则先 revoke 而非物理删）。测试：有未决订单拒删、无未决订单可删且快照保留。
- [ ] **提交** — `fix(strategy): restrict deletion with outstanding payment orders`。

### Task C-3：订单级幂等原子发放服务

**Files:** Create `application/services/payment_grant_service.py`、`tests/test_payment_grant_service.py`；Modify `payment_repository.py`、`payment_service.py`（handle_notify 改为标记 paid+pending 后调用即时发放，失败不抛致 500）。

- [ ] **RED/实现/GREEN** — `apply_grant_atomic` 单事务：锁 order→已 granted 直接返回→按 product_type 只发对应权益→写 PAYMENT audit（`source_order_id`）→`grant_status=granted`+`granted_at`+清 lease，commit。permanent 权益收月费订单标记 granted 但不降级；inactive 用户仍履约（权益入账，账号不可登录）；expired/revoked 续费从 now 清撤销；有效定期从原到期延长。`handle_notify` 改：webhook 事务只可靠置 paid+grant_status=pending+`next_grant_retry_at=paid_at`，随后调 `grant_order` 即时尝试，失败记 retrying 仍返回 ok。测试：会员单只改会员、订阅单只改指定订阅、两类支付前后 role 不变、重复 webhook/重试只发一次、permanent 不降级、inactive 履约。
- [ ] **提交** — `feat(billing): grant payment entitlements idempotently`。

### Task C-4：认领租约 + 崩溃回收 + 8 次退避 + scheduler

**Files:** Modify `payment_grant_service.py`、`payment_repository.py`、`scheduler_jobs.py`（新增 `process_payment_grants_job`）、`scheduler.py`（注册 job）；Create `tests/test_payment_grant_retry.py`；Modify `tests/test_scheduler_jobs_registration.py`。

- [ ] **RED/实现/GREEN** — `claim_next_grant` 用 `FOR UPDATE SKIP LOCKED` 扫 `status='paid' AND grant_status IN ('pending','retrying') AND historical_grant_ambiguous=false AND next_grant_retry_at<=now`，以及 `processing AND grant_lease_until<now` 回收；认领写 processing+lease+owner，自动 worker 递增 `grant_attempts` 与 `automatic_grant_attempts`。退避分钟 `[1,2,5,10,30,60,180,360]`，达 8 次→failed+告警。scheduler 注册 `process_payment_grants_job`（IntervalTrigger，建议 2min，仅 sql 后端）。测试：新 paid due now、processing 崩溃 lease 过期可回收、两 worker SKIP LOCKED 不重复、8 次后 failed、历史歧义不被领。
- [ ] **提交** — `feat(billing): auto-retry failed payment grants`。

### Task C-5：管理员支付管理 + 历史复核 API

**Files:** Modify `api/routers/admin_entitlements.py`、`api/schemas/admin_entitlements.py`、`payment_grant_service.py`；Create `tests/test_admin_payment_grants_api.py`。

- [ ] **RED/实现/GREEN** — `GET /admin/entitlements/payment-grants`（默认 paid 未 granted，筛 user_id/grant_status/from/to，page_size≤200，`paid_at DESC,order_id DESC`）；`POST .../payment-grants/{order_id}/retry`（reason+Idempotency-Key，failed/retrying/pending 非历史歧义；processing 且 lease 未过期 409；granted 返当前不重发；historical ambiguous 409 `billing.grant_review_required`）；`POST .../payment-grants/{order_id}/review`（decision RETRY/MARK_GRANTED，仅 manual_review+ambiguous，写审计）。全 ADMIN。
- [ ] **提交** — `feat(billing): admin payment grant management`。

### Task C-6：自助订单状态展示

**Files:** Modify `payment_service._order_view`（加 `grant_status`/`grant_pending`）、`billingApi.ts`、`BillingPage.tsx`；Modify `tests/test_billing_payment.py`、`frontend/src/test/BillingPage.test.tsx`。

- [ ] **RED/实现/GREEN** — 订单视图加 `grant_status`+`grant_pending`；前端仅 `grant_status=granted` 显示"权益已到账"；paid+pending/processing/retrying 显示"支付已确认，权益发放中"；failed 显示"发放异常，已通知管理员"；不暴露内部错误。
- [ ] **提交** — `feat(billing): show grant status to buyers`。

### Task C-7：PG 并发 + C 门禁 + E2E

**Files:** Create `tests/test_payment_grant_concurrency_pg.py`；Modify `frontend/e2e/admin-entitlements.spec.ts`。

- [ ] **RED/GREEN** — `@pytest.mark.real_pg`：同订单不同 trade_id 并发只发一次；两 worker 认领同订单只一个执行；管理员/支付并发写同用户权益不丢更新；processing lease 竞态。
- [ ] **C 全量门禁** — 专项 pytest + real_pg + `pytest -q` + `ruff` + `mypy` + 前端 build；`alembic current`=0031。
- [ ] **E2E 追加** — 模拟发放失败→自动重试成功；历史歧义订单须显式复核；自助页 grant_status 展示。
- [ ] **提交** — `test(billing): verify payment grant concurrency`。

---

## 生产发布门禁（需另行明确授权）

依序 A→B→C，每单元迁移→起服务→健康检查→专项 smoke 后再下一单元。发布前：数据库备份；三份 migration preflight；历史权益异常清单；subscription 订单孤儿策略清单；历史 `paid+granted_at NULL` 歧义订单只读清单（保持 manual_review，人工逐单决策）；确认无新旧 API 混跑；确认 api/scheduler 同版本；确认策略硬删除受 RESTRICT。0030/0031 产生永久/撤销/复核状态后前向修复，不承诺无损 downgrade。

---

## 规格覆盖自检

| 规格要求 | 对应任务 |
|---|---|
| 0029 auth_version / 会话吊销 | A1、A2、A3 |
| 统一身份解析（中间件+端点一致） | A4 |
| 最后管理员并发锁序 | A3、A5 |
| 0030 永久/撤销/乐观锁/审计表/preflight | B1-1 |
| 统一实时权益谓词 | B1-2 |
| 幂等 operation + 逐资源 audit | B1-3、B1-4、B1-6 |
| 平台策略资格谓词统一 | B1-5 |
| 管理员查询/设置/批量/撤销 API | B1-6~B1-8、B2-4 |
| 角色/旧 grant 接入审计事务 | B1-9 |
| 会员撤销停新开仓/停实例/释放预留/保留退出 | B2-1~B2-4 |
| USER 补齐 + 抽屉四区 + 双语 | B3-2~B3-4 |
| 幂等重试/陈旧响应/自降级登出 | B3-1、B3-5 |
| 永久权益自助展示 | B3-6 |
| 0031 发放状态/租约/历史歧义/快照/RESTRICT | C-1、C-2 |
| 订单级原子幂等发放 + 支付不改角色 | C-3 |
| 自动 8 次退避 + 崩溃回收 + 人工兜底 | C-4、C-5 |
| 自助 grant_status 展示 | C-6 |
| PG 并发 + E2E | A5、B1-10、B2-4、B3-7、C-7 |
