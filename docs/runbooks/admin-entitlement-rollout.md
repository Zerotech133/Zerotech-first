# 管理员权限与权益管理 发布手册

- 适用分支：`feat/admin-entitlement-management`
- 设计：`docs/superpowers/specs/2026-09-09-admin-entitlement-management-design.md`
- 计划：`docs/superpowers/plans/2026-09-10-admin-entitlement-management.md`
- 顺序发布三单元：**A（会话版本）→ B（权益/审计）→ C（支付补偿）**，每单元通过门禁后再下一单元。

> ⚠️ 未经明确授权，不对生产（207.56.221.140）执行。本手册用于受控维护窗口。

---

## 单元 A：会话版本与统一身份解析（迁移 0029）

### A.1 变更摘要

- `v2_user` 新增 `auth_version INTEGER NOT NULL DEFAULT 1`（迁移 0029）；
- 签发 token 携带 `auth_version`；角色变更 / 停用 / 改密时原子递增；旧 token 立即失效；
- VIEWER/USER 写限制中间件与端点依赖统一走数据库当前角色（不再读 token payload role）。

### A.2 兼容性与破坏性

- **前向安全迁移**：`0029` 的 `downgrade` 显式抛错（forward-only），不删除安全代数列；回退用前向修复。
- **缺 claim 的旧 token** 仅在该用户 `auth_version` 仍为 1 时兼容；一旦递增即失效。
- **禁止新旧 API 二进制混跑**：旧实例不会递增 `auth_version`，会破坏"改角色即踢旧会话"保证。必须停写→迁移→整体起新实例。
- 角色/停用 API 请求体新增必填 `auth_version`（乐观锁）；`DELETE /auth/users/{id}` 现需 JSON body `{"auth_version": n}`。前端已同步。

### A.3 迁移实演（已在本地 PostgreSQL 16 验证）

```bash
createdb zhice_a6_rehearsal
export DB_MAIN_URL=postgresql://<user>:<pass>@127.0.0.1:5432/zhice_a6_rehearsal
PYTHONPATH=src .venv/bin/alembic upgrade 0028
# 插入一行旧用户（无 auth_version 列）
psql ... -c "INSERT INTO v2_user (user_id,username,password_hash,role,is_active,locale,created_at) VALUES ('mig-user','mig-user','x','USER',true,'zh',now());"
PYTHONPATH=src .venv/bin/alembic upgrade 0029
# 期望：auth_version = NO(not null) / default 1 / 旧行回填 1 / alembic current = 0029 (head)
```

验证结果（实测）：`auth_version|NO|1`，`mig-user|1`，`0029 (head)`。`downgrade` 前向拒绝。

### A.4 受控维护窗口发布步骤

1. 备份数据库。
2. 确认当前迁移版本 = `0028`。
3. **停止 API 与 scheduler**（确保无实例写库）。
4. 执行迁移：`alembic upgrade 0029`；`alembic current` 应为 `0029 (head)`。
5. **整体启动新版 API 与 scheduler**（同一构建/镜像；不得与旧版并存）。
6. 冒烟校验：
   - 迁移前签发的旧 token（版本 1）仍可调用 `GET /api/v1/auth/me`；
   - 新登录 token 含 `auth_version=1`；
   - 改测试用户角色后，其旧 token 调用受保护接口返回 401 `auth.session_revoked`；
   - 该用户重新登录后使用数据库当前角色；
   - 将某用户数据库角色改为 VIEWER/USER 后，全局写限制立即按数据库角色生效。
7. 回退策略：应用回退到明确的兼容版本；**不**执行 `downgrade 0029`（前向修复保留 `auth_version` 列）。

### A.5 门禁（发布前必须全绿）

```bash
# 单元 A 专项
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/test_migration_0029.py tests/test_auth_session_version.py \
  tests/test_user_management.py tests/test_change_password.py \
  tests/test_rbac_viewer.py tests/test_user_role_orm.py \
  tests/test_auth_admin_concurrency_pg.py -q
# 全量
PYTHONPATH=src .venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
.venv/bin/mypy src/zhice_os_v2/api/dependencies.py src/zhice_os_v2/api/routers/auth.py \
  src/zhice_os_v2/application/services/token_service.py \
  src/zhice_os_v2/application/services/auth_service.py \
  src/zhice_os_v2/infrastructure/repositories/user_repository.py
npm --prefix frontend test && npm --prefix frontend run build
# 真实 PostgreSQL 并发（最后管理员锁序，需专用测试库迁到 0029）
REAL_PG=1 DB_MAIN_URL=postgresql://<user>:<pass>@127.0.0.1:5432/<test_db> \
PERSISTENCE_BACKEND=sql PYTHONPATH=src .venv/bin/python -m pytest \
  -o addopts="" -m real_pg tests/test_auth_admin_concurrency_pg.py -q
```

本地实测记录：单元 A 专项 114 passed / 全量 1109 passed / 前端 131 passed + 构建成功 / ruff & mypy(改动文件) 干净 / 真实 PG 并发 4 参数 × 10 轮稳定。

---

## 单元 B / 单元 C

单元 B（迁移 0030：权益/审计）与单元 C（迁移 0031：支付补偿）的发布步骤与门禁在实现后追加到本手册。三单元共同的生产前置检查（数据库备份、迁移 preflight、历史权益异常清单、订单孤儿策略清单、历史 paid-未发放歧义订单只读清单、无新旧混跑、api/scheduler 同版本）见设计文档"生产发布门禁"。
