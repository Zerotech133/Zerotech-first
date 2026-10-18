# 多用户策略运行与 Telegram 定向通知总控实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有全局策略/账本/广播模型渐进改造成公共策略可复用、用户策略实例与账户隔离、Telegram 按实际运行实例定向推送的多用户系统。

**Architecture:** 保留 `Strategy/StrategyVersion` 作为算法定义层，新增 `UserAccount/UserStrategyInstance` 作为运行边界；交易链从计划开始携带 `user_strategy_id/account_id`。通过 `legacy → shadow → enforced` 三态开关渐进切换，Telegram 使用 deep-link 验证绑定与数据库 outbox，任何阶段都不允许半新半旧写入生产。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2、Alembic、PostgreSQL 13/16、APScheduler、React 19、TypeScript 6、Ant Design、Vitest、Playwright、httpx。

**Source spec:** `docs/superpowers/specs/2026-09-06-multi-user-strategy-telegram-design.md`

---

## 计划拆分与依赖图

```text
Plan A: user-strategy-foundation
  0016 schema + USER role + catalog/private strategy + accounts + instances
                 │
                 ▼
Plan B: user-execution-isolation
  0017 ownership columns + ledger reservation + workflow fan-out + owner-scoped reads
                 │
                 ▼
Plan C: telegram-directed-notifications
  0018 binding challenge + webhook + preferences + outbox + directed worker
                 │
                 ▼
Plan D: multi-user-frontend-migration-rollout
  UI integration + legacy backfill + 0019 NOT NULL gate + shadow/canary/production rollout
```

> **迁移号修正（复核后）**：每个任务用独立 Alembic revision（同号追加在已升级环境不会重跑）。Phase A 实际占用 **0016–0020**（0016 角色约束 / 0017 策略归属+发布申请 / 0018 用户账户 / 0019 运行实例 / 0020 访问审计）。因此 Phase B/C/D 的迁移号顺延至 **0021 起**，本文件下方各阶段标注的 0017/0018/0019 为旧编号，待进入对应阶段时以实际链为准重编。

四个执行计划：

- `docs/superpowers/plans/2026-09-06-user-strategy-foundation.md`
- `docs/superpowers/plans/2026-09-06-user-execution-isolation.md`
- `docs/superpowers/plans/2026-09-06-telegram-directed-notifications.md`
- `docs/superpowers/plans/2026-09-06-multi-user-frontend-migration-rollout.md`

## 不可违反的总规则

1. 所有功能使用 TDD：先见到目标测试因缺失行为失败，再写最小实现。
2. 在 0019 约束上线前，所有新交易写入必须已携带 `user_strategy_id/account_id`；历史 NULL 只能来自明确的 legacy 数据。
3. `/my/*` 路由从 JWT `current_user.user_id` 取 owner，不接受请求体覆盖 `user_id`；USER 写中间件只放行 `/api/v1/my/*` 与本人 auth 自助端点。
4. 普通 API 默认 owner-scoped；全局查询只允许显式 `/admin/*` 路由并写审计日志。
5. Telegram 收件人只能由 `user_strategy_id → user_id` 或市场报告的明确用户订阅解析，禁止按 `strategy_version_id` 广播。
6. `TELEGRAM_BOT_TOKEN`、webhook secret、绑定 token 原文不得进入前端、数据库日志或错误响应。
7. 旧 `broadcast_telegram()` 在 directed 模式验证完成前保留；切换后成交/报告调用点必须移除广播路径。
8. 公共版本一旦发布或被实例/交易引用，不允许原地改 spec。
9. 共享账户预算预留必须使用数据库行锁和同事务更新；不得先查询后在另一个事务扣款。
10. 每个生产阶段先备份数据库，再迁移，再 shadow，对比通过后才切换。

### Task 1: 建立干净实施基线

**Files:**
- Inspect: `git status`
- Verify: `docs/superpowers/specs/2026-09-06-multi-user-strategy-telegram-design.md`
- Verify: all four phase plans listed above

- [ ] **Step 1: 确认当前工作区不混入既有未提交部署修改**

Run:

```bash
git status --short --branch
git diff --check
```

Expected: 明确列出已有改动。实施者不得重置或覆盖用户现有改动；应在用户授权后先提交现有工作，或从明确 commit 创建隔离 worktree。

- [ ] **Step 2: 创建专用实施分支/工作树**

Run only after user authorization:

```bash
git switch -c feat/multi-user-strategy-runtime
```

Expected: 当前分支为 `feat/multi-user-strategy-runtime`，且不丢失用户已有改动。

- [ ] **Step 3: 记录测试基线**

Run:

```bash
uv run pytest -q
npm --prefix frontend test -- --run
npm --prefix frontend run build
uv run ruff check src tests
uv run mypy src/zhice_os_v2
```

Expected: 后端、前端、构建、ruff、mypy 均通过；现有 warning 单独记录，不把 warning 误报为失败。

### Task 2: 执行 Plan A——用户策略基础

**Files:**
- Follow: `docs/superpowers/plans/2026-09-06-user-strategy-foundation.md`

- [ ] **Step 1: 完成 0016 与领域/仓储/API**

Expected output contract:

```text
Role.USER exists
public registration => USER
VIEWER remains readonly
public catalog is readable
private strategy is owner-only
user account is persistent
user strategy instance is owner-bound
execution mode remains legacy by default
```

- [ ] **Step 2: 运行阶段门禁**

Run:

```bash
uv run pytest -q tests/test_auth_register.py tests/test_user_management.py \
  tests/test_strategy_ownership.py tests/test_user_accounts.py tests/test_user_strategy_instances.py
uv run alembic upgrade head
uv run alembic current
```

Expected: tests PASS；migration 为 `0016 (head)`。

- [ ] **Step 3: 阶段提交（经用户授权）**

```bash
git add migrations/versions/0016_multi_user_strategy_foundation.py \
  src/zhice_os_v2/domain src/zhice_os_v2/infrastructure \
  src/zhice_os_v2/application src/zhice_os_v2/api tests
git commit -m "feat(accounts): add user strategy runtime foundation"
```

### Task 3: 执行 Plan B——交易链归属与账户隔离

**Files:**
- Follow: `docs/superpowers/plans/2026-09-06-user-execution-isolation.md`

- [ ] **Step 1: 完成 0017 与 ownership propagation**

Expected output contract:

```text
new plan/approval/intent/position rows carry user_strategy_id + account_id
fill ownership resolves through order chain
shared account reservations are atomic
legacy mode unchanged by default
shadow mode computes both paths without duplicate orders
zero enabled instances means no execution
```

- [ ] **Step 2: 运行并发与隔离门禁**

Run:

```bash
uv run pytest -q tests/test_execution_ownership.py tests/test_account_reservation.py \
  tests/test_user_data_isolation.py tests/test_scheduler_user_instances.py
REAL_PG=1 uv run pytest -q -m real_pg tests/test_account_reservation.py
```

Expected: 普通与 real-PG 并发测试均 PASS；两个实例无法超额预留同一账户现金。

- [ ] **Step 3: 阶段提交（经用户授权）**

```bash
git add migrations/versions/0017_trade_runtime_ownership.py \
  src/zhice_os_v2 tests
git commit -m "feat(execution): isolate trades by user strategy instance"
```

### Task 4: 执行 Plan C——Telegram 定向通知

**Files:**
- Follow: `docs/superpowers/plans/2026-09-06-telegram-directed-notifications.md`

- [ ] **Step 1: 完成 0018、deep-link、webhook、偏好和 outbox worker**

Expected output contract:

```text
single platform bot token
one-time challenge stores only hash
webhook secret required
chat_id unique per user
event/delivery idempotent
directed mode resolves exact owner
Telegram failures never rollback trades
broadcast remains only behind explicit legacy mode
```

- [ ] **Step 2: 运行安全与重试门禁**

Run:

```bash
uv run pytest -q tests/test_telegram_binding.py tests/test_notification_outbox.py \
  tests/test_telegram_directed.py tests/test_user_reports.py
uv run ruff check src/zhice_os_v2/application/services/telegram_* \
  src/zhice_os_v2/api/routers/integrations_telegram.py tests/test_telegram_*.py
```

Expected: 绑定码过期/复用/冒用、错误 webhook secret、chat_id 冲突、429 retry、401/403 fail、重复 delivery 都有通过测试。

- [ ] **Step 3: 阶段提交（经用户授权）**

```bash
git add migrations/versions/0018_telegram_binding_outbox.py \
  src/zhice_os_v2 frontend tests .env.example .env.prod.example docker-compose.prod.yml
git commit -m "feat(telegram): route notifications by user strategy"
```

### Task 5: 执行 Plan D——前端、回填和生产切换

**Files:**
- Follow: `docs/superpowers/plans/2026-09-06-multi-user-frontend-migration-rollout.md`

- [ ] **Step 1: 完成前端策略广场、我的策略、运行实例、账户和 Telegram link binding**

Expected: USER 只能看到自己的资源；VIEWER 只读；ADMIN 管理入口独立。

- [ ] **Step 2: 在 production copy 上执行 legacy backfill dry-run**

Run:

```bash
uv run python scripts/backfill_multi_user_ownership.py --dry-run --json-out /tmp/ownership-dry-run.json
python3 -m json.tool /tmp/ownership-dry-run.json
```

Expected: 输出每表 `source/assignable/unassigned/conflicts`；`conflicts=0`，且没有写数据库。

- [ ] **Step 3: 备份并执行 backfill**

Run:

```bash
uv run python scripts/backup_db.py
uv run python scripts/backfill_multi_user_ownership.py --apply --json-out /tmp/ownership-apply.json
uv run python scripts/verify_multi_user_ownership.py --strict
```

Expected: 可归属历史数据全部进入 admin legacy account/instance；不可归属数据进入明确的 legacy-unassigned placeholder；无跨用户映射。

- [ ] **Step 4: 仅在 strict verifier 通过后增加 0019**

Run:

```bash
uv run alembic upgrade 0019
uv run python scripts/verify_multi_user_ownership.py --strict
```

Expected: 0019 将新交易归属列改为 NOT NULL；升级后 verifier 仍 PASS。

- [ ] **Step 5: 阶段提交（经用户授权）**

```bash
git add migrations/versions/0019_enforce_trade_ownership.py scripts \
  frontend/src tests docs
git commit -m "feat(ui): complete multi-user strategy workflows"
```

## 生产渐进切换矩阵

| 阶段 | `MULTI_USER_EXECUTION_MODE` | `TELEGRAM_NOTIFICATION_MODE` | 行为 |
|---|---|---|---|
| A | `legacy` | `broadcast` | 新表/API 可用，旧执行与旧通知不变 |
| B | `shadow` | `broadcast` | 计算实例 fan-out 和 owner，但不产生实例订单 |
| C | `shadow` | `shadow` | 同时计算定向收件人，不发送定向消息 |
| D | `enforced` | `shadow` | 用户交易隔离生效，通知仍只对比 |
| E | `enforced` | `directed` | 定向通知生效，广播调用关闭 |

禁止组合：

```text
MULTI_USER_EXECUTION_MODE=legacy + TELEGRAM_NOTIFICATION_MODE=directed
```

因为 legacy 交易没有可靠 `user_strategy_id`，无法解析唯一收件人。配置验证应在应用启动时拒绝该组合。

## 每个环境的上线门禁

### 本地/测试

```bash
uv run pytest -q
npm --prefix frontend test -- --run
npm --prefix frontend run build
uv run ruff check src tests
uv run mypy src/zhice_os_v2
```

### 预发布数据库

```bash
uv run alembic current
uv run alembic check
REAL_PG=1 uv run pytest -q -m real_pg
uv run python scripts/verify_multi_user_ownership.py --strict
```

### 生产 canary

- 先只给 admin legacy 实例开启 shadow；
- 连续至少一个完整市场周期比较 legacy 与 shadow：候选、计划、风控、订单、成交数量与金额；
- 定向通知 shadow 记录 `event_id/expected_user_id/reason`，不得记录 chat_id；
- 任何跨用户命中、无 owner 新写入、预算超限或重复通知立即回退开关；
- 切 directed 前确认 `broadcast_telegram` 的成交/报告调用计数为 0。

## 最终验收场景

1. 用户 A/B 选择同一公共版本，各自产生不同 instance/account。
2. 同一输入产生共享候选，但计划、订单、成交、持仓按 A/B 分离。
3. A 无法 GET/PATCH/approve B 的资源，响应 404 或统一 owner-forbidden，不泄露资源存在性。
4. A 使用独立账户、B 使用共享组合账户，资金与风控口径正确。
5. 并发策略预留不会使 `cash_balance - reserved_cash < 0`。
6. 私有策略只有 owner 可见；公开审核派生公共副本，原私有实例不变。
7. 公共新版本只产生 upgrade 提示，用户确认前不切换。
8. Telegram deep-link 成功绑定；过期/复用/冒用失败。
9. A 的成交只产生 A 的 delivery；B 关闭 fill 后无 delivery。
10. Telegram 失败使 delivery RETRY/FAILED，但 fill 仍已提交。
11. 中文/英文用户收到对应 locale 模板。
12. legacy 历史逐表计数不变，所有历史记录可解释归属。

## 最终发布前命令

```bash
uv run pytest -q
REAL_PG=1 uv run pytest -q -m real_pg
npm --prefix frontend test -- --run
npm --prefix frontend run build
uv run ruff check src tests
uv run mypy src/zhice_os_v2
uv run alembic heads
uv run python scripts/verify_multi_user_ownership.py --strict
git diff --check
```

Expected: 全部 exit 0；Alembic 单一 head `0019`；strict verifier 无 NULL owner、无跨用户引用、无账户负可用资金、无重复 delivery。
