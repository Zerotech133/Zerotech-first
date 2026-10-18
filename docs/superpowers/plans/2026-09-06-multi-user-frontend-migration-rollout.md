# 多用户前端、旧数据迁移与生产切换 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成策略广场、我的策略、账户、运行实例和 Telegram deep-link 前端；幂等回填旧数据，收紧 ownership 约束，并按 shadow/canary 顺序切换生产。

**Architecture:** 前端调用 owner-scoped `/my/*` API；旧数据先 dry-run、备份、回填到 admin legacy 账户/实例，再由严格 verifier 证明完整性后执行 0024 NOT NULL。生产依次切 execution shadow、notification shadow、execution enforced、notification directed。

**Tech Stack:** React 19、TypeScript 6、Ant Design、i18next、Vitest、Playwright、Python CLI、PostgreSQL/Alembic。

> **迁移号（A=0016-0020、B=0021-0022、C=0023 已用）**：Phase D 的 NOT NULL 收紧门 = **0024**（down_revision=0023）。下文旧编号 0019 以 0024 为准。**安全铁律**：0024 仅在回填完成且 strict verifier 通过后执行；前端全部走 owner-scoped /my/*；回填脚本 dry-run 默认、--apply 需显式；生产切换顺序 execution shadow → notification shadow → execution enforced → notification directed（每步前备份数据库）。

---

## 文件结构

**Create:**
- `frontend/src/pages/StrategyCatalogPage.tsx`
- `frontend/src/pages/MyStrategiesPage.tsx`
- `frontend/src/pages/MyStrategyInstancesPage.tsx`
- `frontend/src/pages/MyAccountsPage.tsx`
- `frontend/src/components/strategy/EnableStrategyWizard.tsx`
- `frontend/src/components/strategy/NotificationPreferenceForm.tsx`
- `frontend/src/components/TelegramDeepLinkBind.tsx`
- `frontend/src/services/myStrategiesApi.ts`
- `frontend/src/services/myAccountsApi.ts`
- `frontend/src/services/notificationsApi.ts`
- `frontend/src/test/MultiUserStrategyFlow.test.tsx`
- `frontend/src/test/TelegramDeepLinkBind.test.tsx`
- `scripts/backfill_multi_user_ownership.py`
- `scripts/verify_multi_user_ownership.py`
- `tests/test_ownership_backfill.py`
- `migrations/versions/0024_enforce_trade_ownership.py`
- `docs/runbooks/multi-user-rollout.md`

**Modify:** `frontend/src/App.tsx`, `StrategiesPage.tsx`, `ProfilePage.tsx`, `TelegramBind.tsx`, `useRole.ts`, navigation, i18n and API service files.

### Task 1: 前端 USER 权限能力模型

- [ ] **Step 1: 写 RED**

```typescript
it('USER can manage own resources but not platform resources', () => {
  localStorage.setItem('zhice_role', 'USER');
  expect(getRoleCapabilities()).toEqual(expect.objectContaining({
    canManageOwn: true,
    canManagePlatform: false,
    isReadOnly: false,
  }));
});
```

Also assert VIEWER remains read-only.

- [ ] **Step 2: 实现 useRole capabilities**

Return role/isViewer/isUser/canManageOwn/canManagePlatform/canWrite. Keep `canWrite` for existing callers.

- [ ] **Step 3: GREEN**

```bash
npm --prefix frontend test -- --run src/test/UserManagementSecurity.test.tsx
```

### Task 2: Typed owner-scoped API clients

- [ ] **Step 1: 写 API contract RED**

Mock fetch and assert all ordinary endpoints start `/my/` and bodies exclude user_id.

- [ ] **Step 2: Create DTO clients**

Define catalog/version, account, instance, preference and challenge/status DTOs. Reuse `api<T>()`; no direct fetch.

- [ ] **Step 3: GREEN**

```bash
npm --prefix frontend test -- --run src/test/MultiUserStrategyFlow.test.tsx
```

### Task 3: 策略广场与启用向导

- [ ] **Step 1: 写 UI RED**

```typescript
it('enables public strategy with dedicated account by default', async () => {
  renderCatalog();
  await user.click(screen.getByRole('button', { name: '使用策略' }));
  expect(screen.getByLabelText('独立策略子账户')).toBeChecked();
  await user.type(screen.getByLabelText('初始资金'), '100000');
  await user.click(screen.getByRole('button', { name: '确认启用' }));
  expect(createInstance).toHaveBeenCalledWith(expect.objectContaining({
    account_mode: 'NEW_DEDICATED', initial_capital: 100000,
  }));
});
```

Cover shared account, risk params, execution mode, notifications and validation.

- [ ] **Step 2: Implement wizard**

Steps: version→account mode→capital/quota+risk→execution→notifications→confirm. Default dedicated.

- [ ] **Step 3: Add strategy tabs**

```text
/:market/strategies?tab=catalog
/:market/strategies?tab=mine
/:market/strategies?tab=running
/:market/strategies?tab=accounts
```

Keep current eight-group shell.

- [ ] **Step 4: GREEN/build**

```bash
npm --prefix frontend test -- --run src/test/MultiUserStrategyFlow.test.tsx
npm --prefix frontend run build
```

### Task 4: 私有策略、公开申请和版本升级 UI

- [ ] **Step 1: RED cases**

Alice sees own private only; public rows have no edit/delete; immutable edit creates new version; request cannot submit twice; upgrade requires confirmation and shows “旧持仓平仓后升级”.

- [ ] **Step 2: Implement pages**

Publication success explains derived public copy; original private strategy and instance stay unchanged.

- [ ] **Step 3: GREEN**

```bash
npm --prefix frontend test -- --run src/test/MultiUserStrategyFlow.test.tsx -t "private|publication|version|upgrade"
```

### Task 5: 我的账户与运行实例 UI

- [ ] **Step 1: RED cases**

Dedicated/shared badges, available cash calculation, enable/disable, quota validation, account total risk, upgrade banner.

- [ ] **Step 2: Implement pages**

Shared view groups instances and quota utilization. Dedicated view has one instance. Account IDs are selections from owner API, never arbitrary text.

- [ ] **Step 3: GREEN/build**

```bash
npm --prefix frontend test -- --run src/test/MultiUserStrategyFlow.test.tsx
npm --prefix frontend run build
```

### Task 6: Telegram deep-link UI

- [ ] **Step 1: RED**

```typescript
it('opens one-time bot link and polls binding status', async () => {
  createChallenge.mockResolvedValue({
    url: 'https://t.me/test_bot?start=synthetic-token',
    expires_at: '2026-09-06T10:10:00Z',
  });
  renderTelegramBind();
  await user.click(screen.getByRole('button', { name: '绑定 Telegram' }));
  expect(window.open).toHaveBeenCalledWith(expect.stringContaining('https://t.me/test_bot?start='), '_blank', 'noopener,noreferrer');
});
```

Cover expiry/regenerate, bound/unbind, duplicate chat, bot not configured and absence of manual chat_id input.

- [ ] **Step 2: Replace component**

Show bot username, expiry countdown and status check. Never display token/chat ID. Poll only while active; stop on unmount/bound/expired.

- [ ] **Step 3: Add preferences and GREEN**

User-level in Profile; instance-level in running strategies.

```bash
npm --prefix frontend test -- --run src/test/TelegramDeepLinkBind.test.tsx
npm --prefix frontend run build
```

### Task 7: 旧数据回填脚本 TDD

- [ ] **Step 1: dry-run/no-write RED**

```python
def test_dry_run_reports_without_writes(backfill, db_snapshot):
    report = backfill.run(dry_run=True)
    assert report["v2_trade_plan"]["source"] > 0
    assert database_hash() == db_snapshot


def test_backfill_is_idempotent(backfill):
    first = backfill.run(dry_run=False)
    second = backfill.run(dry_run=False)
    assert second["updated_total"] == 0
    assert ownership_counts() == first["final_counts"]
```

- [ ] **Step 2: deterministic mapping**

Create one admin legacy account per market and one legacy instance per `(strategy_version_id,market)` using stable `fit_id`. Map direct entities, then request/execution/fill by chain. Unresolved goes to explicit admin `legacy-unassigned-<market>`, never an ordinary user.

- [ ] **Step 3: CLI**

```text
--dry-run default rollback-only
--apply required to commit
--json-out PATH
--admin-user admin
--strict
```

Report per table source/already_owned/assignable/updated/unassigned/conflicts.

- [ ] **Step 4: GREEN**

```bash
uv run pytest -q tests/test_ownership_backfill.py
```

### Task 8: 严格 verifier

- [ ] **Step 1: Write defect fixtures**

NULL owner, cross-user account, market mismatch, orphan chain, negative available cash, quota overflow, duplicate delivery.

- [ ] **Step 2: Implement read-only verifier**

Works on PG13/16; JSON output; strict exit 1; never writes.

- [ ] **Step 3: GREEN**

```bash
uv run pytest -q tests/test_ownership_backfill.py -k verifier
uv run python scripts/verify_multi_user_ownership.py --strict
```

### Task 9: 0024 NOT NULL gate

- [ ] **Step 1: migration precondition RED**

0024 refuses if equivalent strict checks fail.

- [ ] **Step 2: Implement 0024**

Set ownership columns NOT NULL on plan/approval/intent/position/replay after backfill; add useful consistency indexes. Do not duplicate user_id on request/execution/fill.

- [ ] **Step 3: rehearsal**

```bash
uv run python scripts/backfill_multi_user_ownership.py --dry-run --json-out /tmp/ownership.json
uv run python scripts/backfill_multi_user_ownership.py --apply --json-out /tmp/ownership-apply.json
uv run python scripts/verify_multi_user_ownership.py --strict
uv run alembic upgrade head
uv run python scripts/verify_multi_user_ownership.py --strict
```

### Task 10: Runbook and production rollout

- [ ] **Step 1: Create runbook**

Include backup/migration/env/restart/health/shadow/rollback for Docker and 159 systemd. Record that 159 HTTP and 113 self-signed HTTPS cannot enable Telegram webhook binding until each has trusted public HTTPS. Independent database deployments use separate Bots in phase 1; no shared central webhook. No real secrets.

- [ ] **Step 2: Execution shadow one full market cycle**

Compare legacy/shadow candidates, plans, risk, orders, value. owner rate 100%, missing/cross-user 0.

- [ ] **Step 3: Enforced canary**

Admin legacy first, then synthetic A/B on same public strategy; verify isolated cash/positions; progressively enable users.

- [ ] **Step 4: Telegram shadow then directed**

Canary admin chat; expected recipients match; broadcast call count 0 before full switch. Roll back by changing mode, not deleting outbox.

### Task 11: E2E and final gate

- [ ] **Step 1: Playwright journeys**

Register USER A/B; same public strategy/dedicated accounts; private owner isolation; publication derived copy; owned fill isolation; Telegram simulated webhook; preference suppression; locale templates.

- [ ] **Step 2: final commands**

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

Expected: all exit 0; single head 0024.

- [ ] **Step 3: commit with authorization**

```bash
git add migrations/versions/0024_enforce_trade_ownership.py scripts frontend/src tests docs/runbooks
git commit -m "feat(ui): complete multi-user strategy workflows"
```
