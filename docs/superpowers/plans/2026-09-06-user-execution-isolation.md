# 用户交易执行隔离 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让每个新计划、审批、订单意图、持仓和复盘都归属于明确的用户策略实例与账户，并支持独立子账户、共享账户配额和原子资金预留。

**Architecture:** 迁移先增加 nullable ownership columns；legacy 模式保持旧执行，shadow 模式只计算实例 fan-out，enforced 模式才写实例交易。共享账户的资金预留在 PostgreSQL 行锁事务内完成，owner-scoped API 从 JWT 限定资源。

> **迁移号（Phase A 已占 0016-0020）**：每任务独立 revision。Task 1 归属列 = **0021**（down_revision=0020）；Task 3 预留表 = **0022**（down_revision=0021）。链为 …0020→0021→0022。下文出现的 `0017` 为旧编号，以 0021/0022 为准。**安全第一铁律**：`multi_user_execution_mode` 默认 legacy，legacy 路径必须与改造前**逐字回归一致**；shadow 仅算指标不写；enforced 才是新隔离路径。

**Tech Stack:** SQLAlchemy/PostgreSQL transactions、FastAPI、APScheduler、pytest、real-PG integration tests。

---

## 文件结构

**Create:**
- `migrations/versions/0021_trade_runtime_ownership.py`（Task 1：五表 nullable 归属列）
- `migrations/versions/0022_account_reservation.py`（Task 3：v2_account_reservation 表）
- `src/zhice_os_v2/application/services/account_ledger_service.py`
- `src/zhice_os_v2/application/services/user_instance_execution_service.py`
- `src/zhice_os_v2/application/services/ownership_resolver.py`
- `src/zhice_os_v2/api/dependencies_ownership.py`
- `src/zhice_os_v2/api/routers/my_trading.py`
- `tests/test_execution_ownership.py`
- `tests/test_account_reservation.py`
- `tests/test_user_data_isolation.py`
- `tests/test_scheduler_user_instances.py`
- `tests/test_shadow_execution.py`

**Modify:**
- `src/zhice_os_v2/infrastructure/db/models.py`
- `src/zhice_os_v2/domain/planning/trade_plan.py`
- `src/zhice_os_v2/domain/portfolio/position.py`
- `src/zhice_os_v2/application/services/workflow_service.py`
- `src/zhice_os_v2/application/services/execution_service.py`
- `src/zhice_os_v2/application/services/trade_plan_service.py`
- `src/zhice_os_v2/application/services/approval_center.py`
- `src/zhice_os_v2/application/services/risk_service.py`
- `src/zhice_os_v2/application/services/account_stats_service.py`
- `src/zhice_os_v2/application/services/pnl_calendar_service.py`
- `src/zhice_os_v2/infrastructure/repositories/sqlalchemy_db.py`
- `src/zhice_os_v2/api/routers/plans.py`, `approval.py`, `positions.py`, `trade.py`, `accounts_ext.py`, `portfolio.py`, `replay.py`, `reports.py`
- `src/zhice_os_v2/scheduler_jobs.py`

### Task 1: 增加交易 ownership schema 与领域字段

- [ ] **Step 1: 写持久化 RED 测试**

Create `tests/test_execution_ownership.py`：

```python
def test_new_plan_persists_runtime_ownership(sql_db, user_instance):
    plan = make_plan(
        user_strategy_id=user_instance.user_strategy_id,
        account_id=user_instance.account_id,
    )
    sql_db.save_trade_plan(plan)
    stored = sql_db.get_trade_plan(plan.plan_id)
    assert stored.user_strategy_id == user_instance.user_strategy_id
    assert stored.account_id == user_instance.account_id


def test_fill_owner_resolves_through_order_chain(ownership_resolver, filled_chain, user_instance):
    owner = ownership_resolver.for_fill(filled_chain.fill.fill_id)
    assert owner.user_id == user_instance.user_id
    assert owner.user_strategy_id == user_instance.user_strategy_id
    assert owner.account_id == user_instance.account_id
```

- [ ] **Step 2: RED**

```bash
uv run pytest -q tests/test_execution_ownership.py
```

- [ ] **Step 3: 创建 0017 nullable columns**

Add `user_strategy_id` and `account_id` to `v2_trade_plan`, `v2_approval_request`, `v2_order_intent`, `v2_position`, `v2_replay`. Each is nullable during compatibility phase, indexed, and has FK to instance/account. Do not duplicate them on request/execution/fill; those resolve through intent.

- [ ] **Step 4: 映射 ORM/领域/repository**

Add optional fields with exact names to TradePlan, Position, approval DTO and mappings. Every `save_*` and row conversion must round-trip them.

- [ ] **Step 5: GREEN**

```bash
uv run pytest -q tests/test_execution_ownership.py tests/test_sqlalchemy_db.py
```

### Task 2: 实现 owner resolver

- [ ] **Step 1: 写每条链路解析 RED**

```python
@pytest.mark.parametrize("entity_type", ["plan", "approval", "intent", "request", "execution", "fill", "position", "replay"])
def test_resolve_owner_for_trade_entity(ownership_resolver, owned_trade_graph, entity_type):
    owner = ownership_resolver.resolve(entity_type, owned_trade_graph.id_for(entity_type))
    assert owner.user_id == owned_trade_graph.user_id
```

Also test missing or legacy owner returns `None`, never guesses by strategy_version_id.

- [ ] **Step 2: 实现 `OwnershipResolver`**

```python
@dataclass(frozen=True)
class RuntimeOwner:
    user_id: str
    user_strategy_id: str
    account_id: str
    strategy_version_id: str
```

Resolution: direct for plan/approval/intent/position/replay; request→intent; execution→request→intent; fill→execution→request→intent.

- [ ] **Step 3: GREEN**

```bash
uv run pytest -q tests/test_execution_ownership.py
```

### Task 3: 实现账户原子资金预留

- [ ] **Step 1: 写独立/共享账户与并发 RED 测试**

```python
def test_reserve_reduces_available_cash(ledger, shared_account, instance):
    reservation = ledger.reserve(shared_account.account_id, instance.user_strategy_id, Decimal("30000"), "plan-1")
    account = ledger.get(shared_account.account_id)
    assert reservation.amount == Decimal("30000")
    assert account.cash_balance - account.reserved_cash == Decimal("70000")


def test_strategy_quota_is_enforced(ledger, shared_account, instance):
    instance.capital_quota = Decimal("20000")
    with pytest.raises(ValueError, match="account.strategy_quota_exceeded"):
        ledger.reserve(shared_account.account_id, instance.user_strategy_id, Decimal("20001"), "plan-2")
```

Real-PG concurrent test launches two sessions reserving 70k from 100k; exactly one succeeds.

- [ ] **Step 2: 创建 0017 `v2_account_reservation`**

Required fields/constraints:

```text
reservation_id UNIQUE
account_id FK
user_strategy_id FK
idempotency_key UNIQUE
amount > 0
status IN RESERVED/CONSUMED/RELEASED
created_at/updated_at
```

- [ ] **Step 3: 实现同事务行锁**

`reserve()` locks account with `FOR UPDATE`, verifies owner/account/quota/available cash, increments reserved_cash, inserts reservation in one transaction. `consume()` decrements reserved_cash and cash_balance; `release()` decrements reserved_cash. Duplicate idempotency key returns existing reservation.

- [ ] **Step 4: GREEN including real PG**

```bash
uv run pytest -q tests/test_account_reservation.py -m "not real_pg"
REAL_PG=1 uv run pytest -q tests/test_account_reservation.py -m real_pg
```

### Task 4: 将共享候选 fan-out 到用户实例

- [ ] **Step 1: 写 fan-out RED**

```python
def test_shared_candidate_creates_one_plan_per_enabled_instance(execution_service, shared_candidate, two_instances):
    plans = execution_service.materialize_plans(shared_candidate, two_instances)
    assert {p.user_strategy_id for p in plans} == {i.user_strategy_id for i in two_instances}
    assert len({p.plan_id for p in plans}) == 2


def test_no_enabled_instance_creates_no_plan(execution_service, shared_candidate):
    assert execution_service.materialize_plans(shared_candidate, []) == []
```

- [ ] **Step 2: 实现 `UserInstanceExecutionService`**

Responsibilities: load enabled instances, merge allowed runtime parameters, group shareable candidate calculations, create per-instance unique plan IDs, reserve budget, persist ownership.

Use:

```python
plan_id = fit_id("plan", candidate_id, user_strategy_id, max_length=64)
```

- [ ] **Step 3: 接入 workflow mode gate**

Refactor the existing pre-market body into `_run_legacy_pre_market(market, trade_date, strategy_types)` without changing its logic, then dispatch explicitly:

```python
if mode == "legacy":
    return self._run_legacy_pre_market(market, trade_date, strategy_types)
if mode == "shadow":
    preview = instance_service.preview_fanout(market, trade_date, strategy_types)
    logger.info("multi_user_shadow %s", preview.to_metrics())
    return self._run_legacy_pre_market(market, trade_date, strategy_types)
return instance_service.execute_fanout(market, trade_date, strategy_types)
```

`preview_fanout()` is pure/read-only and returns counts/fingerprints only. It must not reserve cash or write plans, approvals, orders, positions or notification events.

- [ ] **Step 4: 修复零 ACTIVE 回退**

`_active_strategy_types()` returns `None` only on DB failure and `[]` for zero active. Caller may fail-open only on `None`; `[]` skips all.

- [ ] **Step 5: GREEN**

```bash
uv run pytest -q tests/test_scheduler_user_instances.py tests/test_shadow_execution.py \
  tests/test_pre_market_align.py tests/test_trade_pipeline.py
```

### Task 5: 贯穿审批、执行、成交、持仓与复盘

- [ ] **Step 1: 写闭环 RED**

```python
def test_owned_plan_reaches_owned_position(workflow, user_instance):
    result = workflow.run_owned_trade(user_instance, signal="BUY")
    assert result.plan.user_strategy_id == user_instance.user_strategy_id
    assert result.approval.user_strategy_id == user_instance.user_strategy_id
    assert result.intent.user_strategy_id == user_instance.user_strategy_id
    assert result.position.user_strategy_id == user_instance.user_strategy_id
    assert result.position.account_id == user_instance.account_id
```

- [ ] **Step 2: 引入统一 `RuntimeContext`**

```python
@dataclass(frozen=True)
class RuntimeContext:
    user_id: str
    user_strategy_id: str
    account_id: str
    strategy_version_id: str
    execution_mode: str
    auto_execute: bool
```

Pass this object through plan/risk/approval/execution instead of unrelated strings.

- [ ] **Step 3: consume/release reservation**

Successful fill consumes reservation in the same transaction as fill/position persistence where possible; rejection/cancel/error releases idempotently.

- [ ] **Step 4: Position/PnL/Replay 按账户归属**

Filter account stats and PnL calendar by account/user_strategy. Replay inherits ownership. Legacy rows remain admin-only until backfill.

- [ ] **Step 5: GREEN**

```bash
uv run pytest -q tests/test_execution_ownership.py tests/test_trade_pipeline.py \
  tests/test_approve_plan_auto_approval.py tests/test_position_ops.py \
  tests/test_pnl_calendar.py tests/test_execution_fee.py
```

### Task 6: Owner-scoped API 与跨用户隔离

- [ ] **Step 1: 写 A/B 隔离 RED**

```python
@pytest.mark.parametrize("path", [
    "/api/v1/my/plans/{id}", "/api/v1/my/positions/{id}",
    "/api/v1/my/replays/{id}", "/api/v1/my/approvals/{id}",
])
def test_bob_cannot_read_alice_resource(path, alice_resource, bob_headers, client):
    response = client.get(path.format(id=alice_resource.id), headers=bob_headers)
    assert response.status_code == 404
```

Add mutation, approve and list leakage cases.

- [ ] **Step 2: 实现 owner dependency**

```python
def require_owned(loader):
    def dependency(resource_id: str, current_user: User = Depends(get_current_user)):
        resource = loader(resource_id, current_user.user_id)
        if resource is None:
            raise HTTPException(status_code=404, detail={"code": "common.not_found"})
        return resource
    return dependency
```

- [ ] **Step 3: 新增 `/my/trading`，管理员全局接口独立**

Normal routes never accept optional user_id. Admin global routes live under `/admin/*` and emit audit events.

- [ ] **Step 4: GREEN**

```bash
uv run pytest -q tests/test_user_data_isolation.py tests/test_rbac_viewer.py tests/test_permission_guards.py
```

### Task 7: 0017 阶段总验证

- [ ] **Step 1: Migration**

```bash
uv run alembic upgrade 0017
uv run alembic current
```

- [ ] **Step 2: Full verification**

```bash
uv run pytest -q
REAL_PG=1 uv run pytest -q -m real_pg tests/test_account_reservation.py
uv run ruff check src tests
uv run mypy src/zhice_os_v2
uv run alembic heads
git diff --check
```

Expected: single head 0017; legacy regression-identical; shadow metrics-only; enforced tests fully owned.

- [ ] **Step 3: Commit with authorization**

```bash
git add migrations/versions/0017_trade_runtime_ownership.py src/zhice_os_v2 tests
git commit -m "feat(execution): isolate trades by user strategy instance"
```
