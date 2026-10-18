# 订阅者跟单模拟盘 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让订阅了内置策略的 USER 自助为该策略开一个"跟单模拟盘"，按平台该策略实时组合权重把自选本金等权重跟进去，得到个人模拟持仓/盈亏；撤订冻结只读、复订恢复；绝不影响订阅信号推送。

**Architecture:** 独立子系统（3 张新表 `v2_mirror_sim_account/position/fill` + 独立再平衡任务 `mirror_sim_job`），与 `v2_user_account`/`v2_user_strategy_instance`、enforced 执行链、`record_fill`/订阅扇出/通知路由**完全解耦**。再平衡引擎每 5 分钟读平台该策略开仓（`ustrat-legacy-*` + `closed_at IS NULL`）算目标权重，向用户账户下模拟单收敛。forward-only 锚点 = `account.created_at`（平台仓 `opened_at > created_at` 才进用户篮子）。

**Tech Stack:** Python/FastAPI/SQLAlchemy/Alembic/APScheduler；PostgreSQL 16；React/TS/antd/vitest。后端根 `src/zhice_os_v2/`，测试根 `tests/`，前端 `frontend/`。

**跑测试**：`PYTHONPATH=src .venv/bin/python -m pytest`；前端**必须在 frontend 目录**跑 `npx vitest run` / `npx tsc -b`。real_pg：`createdb -O zhice <db>`（PG16 属主坑）→ `DB_MAIN_URL=... alembic upgrade 0032` → `REAL_PG=1 DB_MAIN_URL=... PERSISTENCE_BACKEND=sql PYTHONPATH=src .venv/bin/python -m pytest -o addopts="" -m real_pg <file>` → `dropdb`。

**⚠️ 环境**：每次改文件前 Fact-Forcing hook 要答 4 问。分支 `feat/subscription-mirror-sim`（基于 main）。当前 alembic head = `0031`。

---

## File Structure

**新建**
- `migrations/versions/0032_mirror_sim.py` — 三表迁移
- `tests/test_migration_0032.py` — 迁移单测
- `src/zhice_os_v2/domain/mirror/mirror_sim.py` — 领域值对象（dataclass）+ 再平衡纯函数
- `src/zhice_os_v2/infrastructure/repositories/mirror_sim_repository.py` — 仓储（自有表 CRUD + 平台开仓读取）
- `src/zhice_os_v2/application/services/mirror_sim_service.py` — 建账户校验 + 再平衡引擎 + 盯市视图 + 订阅对账
- `tests/test_mirror_sim_service.py` — 引擎/建账户/冻结单测
- `src/zhice_os_v2/api/routers/mirror_accounts.py` — `/my/mirror-accounts` 三端点
- `src/zhice_os_v2/api/schemas/mirror_accounts.py` — 请求体
- `tests/test_mirror_accounts_api.py` — API 单测
- `tests/test_mirror_sim_push_isolation.py` — 推送隔离专项
- `tests/test_mirror_sim_concurrency_pg.py` — real_pg sweep/冻结
- `frontend/src/services/mirrorSimApi.ts` — 前端 API 客户端
- `frontend/src/test/mirrorSimApi.test.ts` — 契约测试
- `frontend/src/test/StrategyDetailMirrorSim.test.tsx` — 详情页跟单区测试

**修改**
- `src/zhice_os_v2/infrastructure/db/models.py` — 加 3 个 ORM 模型
- `src/zhice_os_v2/scheduler_jobs.py` — 加 `mirror_sim_job`
- `src/zhice_os_v2/scheduler.py` — 注册 job
- `src/zhice_os_v2/api/main.py` — 注册 router
- `src/zhice_os_v2/api/dependencies.py` — 加 `get_mirror_sim_service`
- `tests/test_architecture.py` — `mirror_sim_service` 加 KNOWN_VIOLATIONS
- `frontend/src/i18n/locales/{zh,en}/errors.json` — `mirror.*` 错误码
- `frontend/src/i18n/locales/{zh,en}/p_strategy.json` — 详情页跟单文案
- `frontend/src/pages/StrategyDetailPage.tsx` — 开模拟盘按钮 + 我的模拟盘区

---

## 单元 1：数据基座（迁移 + ORM + 领域）

### Task 1：迁移 0032（三表）

**Files:**
- Create: `migrations/versions/0032_mirror_sim.py`
- Test: `tests/test_migration_0032.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_migration_0032.py
"""迁移 0032：跟单模拟盘三表 upgrade/downgrade + 约束（sqlite 结构级）。"""
from __future__ import annotations

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect


def _alembic_cfg(db_url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_0032_revision_chain():
    script = ScriptDirectory.from_config(_alembic_cfg("sqlite://"))
    rev = script.get_revision("0032")
    assert rev.down_revision == "0031"


def test_0032_upgrade_creates_tables(tmp_path):
    from alembic import command

    db = f"sqlite:///{tmp_path/'m.db'}"
    cfg = _alembic_cfg(db)
    command.upgrade(cfg, "0032")
    insp = inspect(create_engine(db))
    tables = set(insp.get_table_names())
    assert {"v2_mirror_sim_account", "v2_mirror_sim_position", "v2_mirror_sim_fill"} <= tables
    cols = {c["name"] for c in insp.get_columns("v2_mirror_sim_account")}
    assert {"mirror_account_id", "user_id", "strategy_id", "market", "initial_capital",
            "cash_balance", "status", "created_at", "updated_at"} <= cols


def test_0032_downgrade_drops_tables(tmp_path):
    from alembic import command

    db = f"sqlite:///{tmp_path/'m.db'}"
    cfg = _alembic_cfg(db)
    command.upgrade(cfg, "0032")
    command.downgrade(cfg, "0031")
    tables = set(inspect(create_engine(db)).get_table_names())
    assert "v2_mirror_sim_account" not in tables
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_migration_0032.py -q`
Expected: FAIL（Can't locate revision '0032'）

- [ ] **Step 3: 写迁移**

```python
# migrations/versions/0032_mirror_sim.py
"""subscription mirror sim: 跟单模拟盘三表 (0032)"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "v2_mirror_sim_account",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("mirror_account_id", sa.String(64), nullable=False, unique=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("v2_user.user_id"), nullable=False),
        sa.Column("strategy_id", sa.String(64), sa.ForeignKey("v2_strategy.strategy_id"), nullable=False),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column("initial_capital", sa.Numeric(24, 8), nullable=False),
        sa.Column("cash_balance", sa.Numeric(24, 8), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("initial_capital > 0", name="ck_v2_mirror_sim_account_capital"),
        sa.CheckConstraint("cash_balance >= 0", name="ck_v2_mirror_sim_account_cash"),
        sa.CheckConstraint("status IN ('active','frozen')", name="ck_v2_mirror_sim_account_status"),
        sa.UniqueConstraint("user_id", "strategy_id", name="uq_v2_mirror_sim_account_user_strategy"),
    )
    op.create_index("ix_v2_mirror_sim_account_user", "v2_mirror_sim_account", ["user_id"])

    op.create_table(
        "v2_mirror_sim_position",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("mirror_account_id", sa.String(64),
                  sa.ForeignKey("v2_mirror_sim_account.mirror_account_id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("quantity", sa.Numeric(24, 8), nullable=False),
        sa.Column("avg_cost", sa.Numeric(24, 8), nullable=False),
        sa.UniqueConstraint("mirror_account_id", "symbol", name="uq_v2_mirror_sim_position_acct_symbol"),
    )

    op.create_table(
        "v2_mirror_sim_fill",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("mirror_account_id", sa.String(64),
                  sa.ForeignKey("v2_mirror_sim_account.mirror_account_id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("quantity", sa.Numeric(24, 8), nullable=False),
        sa.Column("price", sa.Numeric(24, 8), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("side IN ('BUY','SELL')", name="ck_v2_mirror_sim_fill_side"),
    )
    op.create_index("ix_v2_mirror_sim_fill_acct", "v2_mirror_sim_fill", ["mirror_account_id"])


def downgrade() -> None:
    op.drop_index("ix_v2_mirror_sim_fill_acct", table_name="v2_mirror_sim_fill")
    op.drop_table("v2_mirror_sim_fill")
    op.drop_table("v2_mirror_sim_position")
    op.drop_index("ix_v2_mirror_sim_account_user", table_name="v2_mirror_sim_account")
    op.drop_table("v2_mirror_sim_account")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_migration_0032.py -q`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add migrations/versions/0032_mirror_sim.py tests/test_migration_0032.py
git commit -m "feat(mirror-sim): migration 0032 三表"
```

### Task 2：ORM 模型

**Files:**
- Modify: `src/zhice_os_v2/infrastructure/db/models.py`（文件尾部追加 3 个模型）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_mirror_sim_service.py（本任务先只放建表冒烟）
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal


def _mk_engine():
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from zhice_os_v2.infrastructure.db.base import Base

    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    return eng


def test_mirror_orm_roundtrip():
    from sqlalchemy.orm import sessionmaker
    from zhice_os_v2.infrastructure.db import models as orm

    Session = sessionmaker(bind=_mk_engine(), expire_on_commit=False)
    s = Session()
    s.add(orm.MirrorSimAccountModel(
        mirror_account_id="msim-x", user_id="u1", strategy_id="v1-crypto-strat-11",
        market="CRYPTO", initial_capital=Decimal("10000"), cash_balance=Decimal("10000"),
        status="active", created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    ))
    s.commit()
    row = s.query(orm.MirrorSimAccountModel).filter_by(mirror_account_id="msim-x").one()
    assert row.status == "active" and row.cash_balance == Decimal("10000")
    s.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_mirror_sim_service.py::test_mirror_orm_roundtrip -q`
Expected: FAIL（`module 'models' has no attribute 'MirrorSimAccountModel'`）

- [ ] **Step 3: 追加 ORM 模型**（`models.py` 文件末尾，仿现有模型风格）

```python
class MirrorSimAccountModel(Base):
    """跟单模拟账户（订阅者自助开，独立于 v2_user_account/enforced 执行链）。"""
    __tablename__ = "v2_mirror_sim_account"
    __table_args__ = (
        CheckConstraint("initial_capital > 0", name="ck_v2_mirror_sim_account_capital"),
        CheckConstraint("cash_balance >= 0", name="ck_v2_mirror_sim_account_cash"),
        CheckConstraint("status IN ('active','frozen')", name="ck_v2_mirror_sim_account_status"),
        UniqueConstraint("user_id", "strategy_id", name="uq_v2_mirror_sim_account_user_strategy"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mirror_account_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("v2_user.user_id"), nullable=False, index=True)
    strategy_id: Mapped[str] = mapped_column(String(64), ForeignKey("v2_strategy.strategy_id"), nullable=False)
    market: Mapped[str] = mapped_column(String(16), nullable=False)
    initial_capital: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    cash_balance: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MirrorSimPositionModel(Base):
    __tablename__ = "v2_mirror_sim_position"
    __table_args__ = (
        UniqueConstraint("mirror_account_id", "symbol", name="uq_v2_mirror_sim_position_acct_symbol"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mirror_account_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("v2_mirror_sim_account.mirror_account_id", ondelete="CASCADE"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    avg_cost: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)


class MirrorSimFillModel(Base):
    __tablename__ = "v2_mirror_sim_fill"
    __table_args__ = (
        CheckConstraint("side IN ('BUY','SELL')", name="ck_v2_mirror_sim_fill_side"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mirror_account_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("v2_mirror_sim_account.mirror_account_id", ondelete="CASCADE"),
        nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

> 注：`models.py` 顶部已 import `BigInteger/String/Numeric/DateTime/ForeignKey/CheckConstraint/UniqueConstraint/Mapped/mapped_column` 及 `Decimal/datetime`（复用现有 import，勿重复；若确缺再补）。

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_mirror_sim_service.py::test_mirror_orm_roundtrip -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/zhice_os_v2/infrastructure/db/models.py tests/test_mirror_sim_service.py
git commit -m "feat(mirror-sim): ORM 三模型"
```

### Task 3：领域再平衡纯函数

**Files:**
- Create: `src/zhice_os_v2/domain/mirror/__init__.py`（空）、`src/zhice_os_v2/domain/mirror/mirror_sim.py`

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_mirror_sim_service.py`）

```python
def test_target_weights_and_rebalance_math():
    from zhice_os_v2.domain.mirror.mirror_sim import compute_target_mv, rebalance_orders

    # 平台开仓 MV：A=600, B=400（tracked 都在），总=1000 → 权重 A .6 B .4
    plat_mv = {"A": 600.0, "B": 400.0}
    tracked = {"A", "B"}
    total_plat = 1000.0
    equity = 10000.0
    target = compute_target_mv(plat_mv, tracked, total_plat, equity)
    assert target == {"A": 6000.0, "B": 4000.0}

    # 用户空仓、现金 10000，阈值 1% → 买 A 60、B 80
    orders = rebalance_orders(
        target_mv=target, user_qty={}, prices={"A": 100.0, "B": 50.0},
        cash=10000.0, equity=equity, churn_threshold=0.01,
    )
    buys = {o.symbol: o for o in orders if o.side == "BUY"}
    assert abs(buys["A"].quantity - 60.0) < 1e-6
    assert abs(buys["B"].quantity - 80.0) < 1e-6


def test_rebalance_cash_capped_and_threshold():
    from zhice_os_v2.domain.mirror.mirror_sim import rebalance_orders
    # 目标 A=6000 但现金仅 1000 → 买入按现金封顶（10 股@100）
    orders = rebalance_orders(target_mv={"A": 6000.0}, user_qty={}, prices={"A": 100.0},
                              cash=1000.0, equity=1000.0, churn_threshold=0.01)
    assert len(orders) == 1 and abs(orders[0].quantity - 10.0) < 1e-6
    # 差额小于阈值(1%*10000=100)不动：目标 5050 vs 当前 5000（50<100）
    none_orders = rebalance_orders(target_mv={"A": 5050.0}, user_qty={"A": 50.0}, prices={"A": 100.0},
                                   cash=5000.0, equity=10000.0, churn_threshold=0.01)
    assert none_orders == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_mirror_sim_service.py -k "target_weights or cash_capped" -q`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 写领域**

```python
# src/zhice_os_v2/domain/mirror/mirror_sim.py
"""跟单模拟盘领域：目标权重与再平衡下单的纯计算（无 IO）。float 入出，服务层用 Decimal 包装。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RebalanceOrder:
    symbol: str
    side: str      # BUY | SELL
    quantity: float
    price: float


def compute_target_mv(
    platform_mv: dict[str, float], tracked: set[str], total_platform_mv: float, equity: float
) -> dict[str, float]:
    """受跟标的目标市值 =(平台市值/平台总市值)*用户权益。分母=平台全部开仓市值；空/0 时返回 {}。"""
    if total_platform_mv <= 0:
        return {}
    return {
        sym: (platform_mv[sym] / total_platform_mv) * equity
        for sym in tracked
        if sym in platform_mv and platform_mv[sym] > 0
    }


def rebalance_orders(
    *, target_mv: dict[str, float], user_qty: dict[str, float], prices: dict[str, float],
    cash: float, equity: float, churn_threshold: float,
) -> list[RebalanceOrder]:
    """先卖后买向目标市值收敛；买入受现金约束不透支；|Δ市值|<=阈值*权益 不动。"""
    thresh = churn_threshold * equity
    orders: list[RebalanceOrder] = []
    avail = cash

    # 卖：用户持有但（目标更低 或 不在目标集）→ 减仓
    for sym, qty in user_qty.items():
        px = prices.get(sym)
        if not px or qty <= 0:
            continue
        cur_mv = qty * px
        tgt = target_mv.get(sym, 0.0)
        if cur_mv - tgt > thresh:
            sell_qty = min(qty, (cur_mv - tgt) / px)
            if sell_qty > 0:
                orders.append(RebalanceOrder(sym, "SELL", sell_qty, px))
                avail += sell_qty * px

    # 买：目标高于当前 → 加仓，按可用现金封顶
    for sym, tgt in target_mv.items():
        px = prices.get(sym)
        if not px or tgt <= 0:
            continue
        cur_mv = user_qty.get(sym, 0.0) * px
        if tgt - cur_mv > thresh:
            buy_mv = min(tgt - cur_mv, avail)
            if buy_mv > 0:
                buy_qty = buy_mv / px
                orders.append(RebalanceOrder(sym, "BUY", buy_qty, px))
                avail -= buy_mv
    return orders
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_mirror_sim_service.py -k "target_weights or cash_capped" -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/zhice_os_v2/domain/mirror/ tests/test_mirror_sim_service.py
git commit -m "feat(mirror-sim): 领域再平衡纯函数"
```

---

## 单元 2：仓储 + 服务（引擎/建账户/盯市/冻结）

### Task 4：仓储

**Files:**
- Create: `src/zhice_os_v2/infrastructure/repositories/mirror_sim_repository.py`

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_mirror_sim_service.py`；可注入 session 的 fixture）

```python
import pytest


@pytest.fixture()
def repo_env(monkeypatch):
    from sqlalchemy.orm import sessionmaker
    import zhice_os_v2.infrastructure.repositories.mirror_sim_repository as mod

    Session = sessionmaker(bind=_mk_engine(), expire_on_commit=False)
    monkeypatch.setattr(mod, "get_session", lambda: Session())
    return Session, mod


def test_repo_create_and_get(repo_env):
    from datetime import UTC, datetime
    from decimal import Decimal
    Session, mod = repo_env
    repo = mod.MirrorSimRepository()
    now = datetime.now(UTC)
    repo.create_account(mirror_account_id="msim-1", user_id="u1", strategy_id="s1",
                        market="CRYPTO", initial_capital=Decimal("10000"), now=now)
    acc = repo.get_by_user_strategy("u1", "s1")
    assert acc is not None and acc.cash_balance == Decimal("10000") and acc.status == "active"
    assert repo.get_by_user_strategy("u1", "nope") is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_mirror_sim_service.py::test_repo_create_and_get -q`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 写仓储**

```python
# src/zhice_os_v2/infrastructure/repositories/mirror_sim_repository.py
"""跟单模拟盘仓储：自有三表 CRUD + 平台开仓读取（直连 get_session，同 payment_repository 范式）。"""
from __future__ import annotations

import secrets
from datetime import datetime
from decimal import Decimal

from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session

_LEGACY_PREFIX = "ustrat-legacy-"


class MirrorSimRepository:
    def _s(self):
        return get_session()

    @staticmethod
    def new_account_id() -> str:
        return f"msim-{secrets.token_hex(6)}"

    def create_account(self, *, mirror_account_id, user_id, strategy_id, market,
                       initial_capital: Decimal, now: datetime) -> None:
        s = self._s()
        try:
            s.add(orm.MirrorSimAccountModel(
                mirror_account_id=mirror_account_id, user_id=user_id, strategy_id=strategy_id,
                market=market, initial_capital=initial_capital, cash_balance=initial_capital,
                status="active", created_at=now, updated_at=now,
            ))
            s.commit()
        finally:
            s.close()

    def get_by_user_strategy(self, user_id: str, strategy_id: str):
        s = self._s()
        try:
            return (s.query(orm.MirrorSimAccountModel)
                    .filter_by(user_id=user_id, strategy_id=strategy_id).one_or_none())
        finally:
            s.close()

    def list_by_user(self, user_id: str) -> list:
        s = self._s()
        try:
            return (s.query(orm.MirrorSimAccountModel)
                    .filter_by(user_id=user_id)
                    .order_by(orm.MirrorSimAccountModel.created_at.desc()).all())
        finally:
            s.close()

    def list_active(self) -> list:
        s = self._s()
        try:
            return s.query(orm.MirrorSimAccountModel).filter_by(status="active").all()
        finally:
            s.close()

    def list_positions(self, mirror_account_id: str) -> list:
        s = self._s()
        try:
            return (s.query(orm.MirrorSimPositionModel)
                    .filter_by(mirror_account_id=mirror_account_id).all())
        finally:
            s.close()

    def recent_fills(self, mirror_account_id: str, limit: int = 50) -> list:
        s = self._s()
        try:
            return (s.query(orm.MirrorSimFillModel)
                    .filter_by(mirror_account_id=mirror_account_id)
                    .order_by(orm.MirrorSimFillModel.created_at.desc()).limit(limit).all())
        finally:
            s.close()

    def platform_open_positions(self, strategy_id: str) -> list[dict]:
        """平台该策略的开仓（ustrat-legacy-* 且 closed_at IS NULL）→ [{symbol, qty, opened_at, market}]。"""
        s = self._s()
        try:
            vids = {v.strategy_version_id for v in
                    s.query(orm.StrategyVersionModel).filter_by(strategy_id=strategy_id).all()}
            if not vids:
                return []
            rows = (s.query(orm.PositionModel)
                    .filter(orm.PositionModel.strategy_version_id.in_(vids),
                            orm.PositionModel.user_strategy_id.like(f"{_LEGACY_PREFIX}%"),
                            orm.PositionModel.closed_at.is_(None))
                    .all())
            out = []
            for p in rows:
                qty = (p.open_qty or Decimal("0")) - (p.close_qty or Decimal("0"))
                if qty > 0 and (p.direction or "").upper() in ("LONG", "BUY", ""):
                    out.append({"symbol": p.ts_code, "qty": float(qty),
                                "opened_at": p.opened_at, "market": p.market})
            return out
        finally:
            s.close()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_mirror_sim_service.py::test_repo_create_and_get -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/zhice_os_v2/infrastructure/repositories/mirror_sim_repository.py tests/test_mirror_sim_service.py
git commit -m "feat(mirror-sim): 仓储 + 平台开仓读取"
```

### Task 5：服务 — 建账户校验 + 架构白名单

**Files:**
- Create: `src/zhice_os_v2/application/services/mirror_sim_service.py`（本任务先建 `create_account`/`list_accounts` + 校验辅助 + 视图）
- Modify: `tests/test_architecture.py`

- [ ] **Step 1: 写失败测试**

```python
def test_create_account_gating(repo_env, monkeypatch):
    from decimal import Decimal
    import pytest as _pytest
    import zhice_os_v2.application.services.mirror_sim_service as svc_mod

    Session, _ = repo_env
    monkeypatch.setattr(svc_mod, "get_session", lambda: Session(), raising=False)
    monkeypatch.setattr(svc_mod, "_is_subscription_active", lambda uid, sid: uid == "u1" and sid == "s1")
    monkeypatch.setattr(svc_mod, "_is_observable_builtin", lambda sid: sid == "s1")
    monkeypatch.setattr(svc_mod, "_strategy_market", lambda sid: "CRYPTO")

    svc = svc_mod.MirrorSimService()
    err = svc_mod.MirrorSimError

    class U:
        user_id = "u1"

    with _pytest.raises(err) as e:
        svc.create_account(U(), strategy_id="other", initial_capital=Decimal("10000"))
    assert e.value.code == "mirror.subscription_required"

    with _pytest.raises(err) as e:
        svc.create_account(U(), strategy_id="s1", initial_capital=Decimal("0"))
    assert e.value.code == "mirror.invalid_capital"

    view = svc.create_account(U(), strategy_id="s1", initial_capital=Decimal("10000"))
    assert view["strategy_id"] == "s1" and view["status"] == "active"

    with _pytest.raises(err) as e:
        svc.create_account(U(), strategy_id="s1", initial_capital=Decimal("10000"))
    assert e.value.code == "mirror.account_exists"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_mirror_sim_service.py::test_create_account_gating -q`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 写服务骨架**

```python
# src/zhice_os_v2/application/services/mirror_sim_service.py
"""跟单模拟盘服务：建账户校验 + 再平衡引擎 + 订阅对账冻结/恢复 + 盯市视图。

直连 get_session/ORM 读平台持仓（同 subscription_observation_service），已入
test_architecture KNOWN_VIOLATIONS。默认 legacy 执行模式下可用（与 enforced 无交集）。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

from zhice_os_v2.domain.mirror.mirror_sim import compute_target_mv, rebalance_orders
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session
from zhice_os_v2.infrastructure.repositories.mirror_sim_repository import MirrorSimRepository

logger = logging.getLogger(__name__)

CHURN_THRESHOLD = 0.01  # 目标与当前市值差 < 1% 权益不动，防 churn


class MirrorSimError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _now() -> datetime:
    return datetime.now(UTC)


def _as_aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _is_subscription_active(user_id: str, strategy_id: str) -> bool:
    from zhice_os_v2.application.services.subscription_service import SubscriptionService
    return SubscriptionService().is_subscription_active(user_id, strategy_id)


def _is_observable_builtin(strategy_id: str) -> bool:
    s = get_session()
    try:
        row = (s.query(orm.StrategyModel.owner_user_id, orm.StrategyModel.visibility)
               .filter(orm.StrategyModel.strategy_id == strategy_id).one_or_none())
        return row is not None and row[0] is None and row[1] == "PUBLIC"
    finally:
        s.close()


def _strategy_market(strategy_id: str) -> str | None:
    s = get_session()
    try:
        row = (s.query(orm.StrategyModel.market)
               .filter(orm.StrategyModel.strategy_id == strategy_id).one_or_none())
        return row[0] if row else None
    finally:
        s.close()


def _latest_prices(symbols: list[str], market: str) -> dict[str, float]:
    """现价：复用 positions._get_latest_prices（日线基+新鲜快照），延迟 import 避免层耦合。"""
    if not symbols:
        return {}
    from zhice_os_v2.api.routers.positions import _get_latest_prices
    return _get_latest_prices(symbols, market)


def _account_view(acc) -> dict:
    return {
        "mirror_account_id": acc.mirror_account_id,
        "strategy_id": acc.strategy_id,
        "market": acc.market,
        "initial_capital": str(acc.initial_capital),
        "cash_balance": str(acc.cash_balance),
        "status": acc.status,
        "created_at": acc.created_at.isoformat() if acc.created_at else None,
    }


class MirrorSimService:
    def __init__(self, repo: MirrorSimRepository | None = None) -> None:
        self._repo = repo or MirrorSimRepository()

    def create_account(self, user, *, strategy_id: str, initial_capital: Decimal) -> dict:
        if initial_capital is None or Decimal(initial_capital) <= 0:
            raise MirrorSimError("mirror.invalid_capital")
        if not _is_subscription_active(user.user_id, strategy_id):
            raise MirrorSimError("mirror.subscription_required")
        if not _is_observable_builtin(strategy_id):
            raise MirrorSimError("mirror.not_observable_strategy")
        if self._repo.get_by_user_strategy(user.user_id, strategy_id) is not None:
            raise MirrorSimError("mirror.account_exists")
        market = _strategy_market(strategy_id) or "CRYPTO"
        self._repo.create_account(
            mirror_account_id=self._repo.new_account_id(), user_id=user.user_id,
            strategy_id=strategy_id, market=market, initial_capital=Decimal(initial_capital), now=_now(),
        )
        return _account_view(self._repo.get_by_user_strategy(user.user_id, strategy_id))

    def list_accounts(self, user) -> list[dict]:
        return [_account_view(a) for a in self._repo.list_by_user(user.user_id)]
```

- [ ] **Step 4: 跑测试确认通过 + 架构白名单**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_mirror_sim_service.py::test_create_account_gating -q`
Expected: PASS

在 `tests/test_architecture.py` 的 `KNOWN_VIOLATIONS` 集合内加：
```python
    # mirror_sim_service 直连 get_session/ORM 读平台开仓与自有跟单表（同 subscription_observation_service
    # 范式，PersistencePort 无这些聚合/自有表方法），独立子系统不改 legacy/执行链。
    "src/zhice_os_v2/application/services/mirror_sim_service.py",
```
Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_architecture.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/zhice_os_v2/application/services/mirror_sim_service.py tests/test_architecture.py tests/test_mirror_sim_service.py
git commit -m "feat(mirror-sim): 建账户校验 + 架构白名单"
```

### Task 6：服务 — 再平衡引擎 + 订阅对账 + 盯市视图

**Files:**
- Modify: `src/zhice_os_v2/application/services/mirror_sim_service.py`

- [ ] **Step 1: 写失败测试**（forward-only、冻结/恢复、收敛幂等）

```python
def _seed_platform(Session, *, strategy_id="s1", svid="sv1"):
    """建策略/版本 + 两条平台开仓（A 早于账户、B 晚于账户）。返回 t0。"""
    from datetime import UTC, datetime, timedelta
    from decimal import Decimal
    from zhice_os_v2.infrastructure.db import models as orm
    s = Session()
    t0 = datetime(2026, 9, 1, tzinfo=UTC)
    s.add(orm.StrategyModel(strategy_id=strategy_id, strategy_name="x", market="CRYPTO",
                            status="ACTIVE", owner_user_id=None, visibility="PUBLIC",
                            publication_status="PUBLISHED", created_at=t0, updated_at=t0))
    s.add(orm.StrategyVersionModel(strategy_version_id=svid, strategy_id=strategy_id,
                                   version_no="1", market="CRYPTO", status="ACTIVE", spec={}, created_at=t0))
    s.add(orm.PositionModel(position_id="pA", strategy_version_id=svid, market="CRYPTO", ts_code="A",
                            direction="LONG", open_qty=Decimal("6"), open_price=Decimal("100"),
                            status="OPEN", opened_at=t0, user_strategy_id=f"ustrat-legacy-{svid}:CRYPTO"))
    s.add(orm.PositionModel(position_id="pB", strategy_version_id=svid, market="CRYPTO", ts_code="B",
                            direction="LONG", open_qty=Decimal("8"), open_price=Decimal("50"),
                            status="OPEN", opened_at=t0 + timedelta(days=10),
                            user_strategy_id=f"ustrat-legacy-{svid}:CRYPTO"))
    s.commit(); s.close()
    return t0


def test_sweep_forward_only_and_convergence(repo_env, monkeypatch):
    from datetime import UTC, datetime, timedelta
    from decimal import Decimal
    import zhice_os_v2.application.services.mirror_sim_service as svc_mod

    Session, _ = repo_env
    monkeypatch.setattr(svc_mod, "get_session", lambda: Session(), raising=False)
    monkeypatch.setattr(svc_mod, "_is_subscription_active", lambda uid, sid: True)
    monkeypatch.setattr(svc_mod, "_is_observable_builtin", lambda sid: True)
    monkeypatch.setattr(svc_mod, "_strategy_market", lambda sid: "CRYPTO")
    monkeypatch.setattr(svc_mod, "_latest_prices", lambda syms, market: {"A": 100.0, "B": 50.0})

    _seed_platform(Session)
    created = datetime(2026, 9, 6, tzinfo=UTC)  # A(9-1)之后、B(9-11)之前 → 只跟 B
    monkeypatch.setattr(svc_mod, "_now", lambda: created)
    svc = svc_mod.MirrorSimService()

    class U:
        user_id = "u1"
    svc.create_account(U(), strategy_id="s1", initial_capital=Decimal("10000"))

    monkeypatch.setattr(svc_mod, "_now", lambda: created + timedelta(days=11))
    svc.sweep()
    pos = {p["symbol"]: p for p in svc.detail(U(), strategy_id="s1")["positions"]}
    assert "A" not in pos                               # forward-only 排除
    assert abs(float(pos["B"]["quantity"]) - 80.0) < 1e-6  # 目标 4000/50

    svc.sweep()  # 收敛幂等
    pos2 = {p["symbol"]: p for p in svc.detail(U(), strategy_id="s1")["positions"]}
    assert abs(float(pos2["B"]["quantity"]) - 80.0) < 1e-6


def test_freeze_on_revoke_and_resume(repo_env, monkeypatch):
    from datetime import UTC, datetime
    from decimal import Decimal
    import zhice_os_v2.application.services.mirror_sim_service as svc_mod
    Session, _ = repo_env
    monkeypatch.setattr(svc_mod, "get_session", lambda: Session(), raising=False)
    sub = {"v": True}
    monkeypatch.setattr(svc_mod, "_is_subscription_active", lambda uid, sid: sub["v"])
    monkeypatch.setattr(svc_mod, "_is_observable_builtin", lambda sid: True)
    monkeypatch.setattr(svc_mod, "_strategy_market", lambda sid: "CRYPTO")
    monkeypatch.setattr(svc_mod, "_latest_prices", lambda syms, market: {"A": 100.0, "B": 50.0})
    monkeypatch.setattr(svc_mod, "_now", lambda: datetime(2026, 9, 6, tzinfo=UTC))
    _seed_platform(Session)
    svc = svc_mod.MirrorSimService()

    class U:
        user_id = "u1"
    svc.create_account(U(), strategy_id="s1", initial_capital=Decimal("10000"))
    sub["v"] = False
    svc.sweep()
    assert svc.detail(U(), strategy_id="s1")["status"] == "frozen"
    sub["v"] = True
    svc.sweep()
    assert svc.detail(U(), strategy_id="s1")["status"] == "active"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_mirror_sim_service.py -k "sweep_forward or freeze_on" -q`
Expected: FAIL（`sweep`/`detail` 未定义）

- [ ] **Step 3: 追加引擎、对账、盯市视图**（在 `MirrorSimService` 内追加以下方法）

```python
    def sweep(self) -> dict:
        stats = {"processed": 0, "frozen": 0, "resumed": 0, "orders": 0}
        for acc in self._repo.list_active():
            try:
                stats["orders"] += self._rebalance_one(acc.mirror_account_id)
                stats["processed"] += 1
            except Exception:
                logger.exception("mirror sweep 账户失败 %s", acc.mirror_account_id)
        self._reconcile_subscriptions(stats)
        return stats

    def _reconcile_subscriptions(self, stats: dict) -> None:
        """active 但订阅失效→frozen；frozen 但订阅恢复→active。独立事务，不碰 admin 撤订路径。"""
        s = get_session()
        try:
            for acc in s.query(orm.MirrorSimAccountModel).all():
                active = _is_subscription_active(acc.user_id, acc.strategy_id)
                if acc.status == "active" and not active:
                    acc.status = "frozen"; acc.updated_at = _now(); stats["frozen"] += 1
                elif acc.status == "frozen" and active:
                    acc.status = "active"; acc.updated_at = _now(); stats["resumed"] += 1
            s.commit()
        finally:
            s.close()

    def _rebalance_one(self, mirror_account_id: str) -> int:
        """单账户再平衡（per-account 事务）。返回下单数。"""
        s = get_session()
        try:
            acc = (s.query(orm.MirrorSimAccountModel)
                   .filter_by(mirror_account_id=mirror_account_id).with_for_update().one_or_none())
            if acc is None or acc.status != "active":
                return 0
            if not _is_subscription_active(acc.user_id, acc.strategy_id):
                return 0  # 订阅失效本轮不动，交由 _reconcile 冻结

            plat = self._repo.platform_open_positions(acc.strategy_id)
            positions = (s.query(orm.MirrorSimPositionModel)
                         .filter_by(mirror_account_id=mirror_account_id).all())
            symbols = sorted({p["symbol"] for p in plat} | {p.symbol for p in positions})
            prices = _latest_prices(symbols, acc.market)

            plat_mv: dict[str, float] = {}
            tracked: set[str] = set()
            total_plat = 0.0
            for row in plat:
                px = prices.get(row["symbol"])
                if not px:
                    continue
                mv = row["qty"] * px
                plat_mv[row["symbol"]] = plat_mv.get(row["symbol"], 0.0) + mv
                total_plat += mv
                if row["opened_at"] is not None and _as_aware(row["opened_at"]) > _as_aware(acc.created_at):
                    tracked.add(row["symbol"])

            user_qty = {p.symbol: float(p.quantity) for p in positions}
            equity = float(acc.cash_balance) + sum(
                float(p.quantity) * prices.get(p.symbol, 0.0) for p in positions)
            target = compute_target_mv(plat_mv, tracked, total_plat, equity)
            orders = rebalance_orders(
                target_mv=target, user_qty=user_qty, prices=prices,
                cash=float(acc.cash_balance), equity=equity, churn_threshold=CHURN_THRESHOLD,
            )
            if not orders:
                return 0
            self._apply_orders(s, acc, positions, orders)
            acc.updated_at = _now()
            s.commit()
            return len(orders)
        except Exception:
            s.rollback(); raise
        finally:
            s.close()

    def _apply_orders(self, s, acc, positions, orders) -> None:
        pos_by_sym = {p.symbol: p for p in positions}
        now = _now()
        for o in orders:
            qty = Decimal(str(o.quantity)); px = Decimal(str(o.price))
            if o.side == "SELL":
                p = pos_by_sym.get(o.symbol)
                if p is None:
                    continue
                sell = min(p.quantity, qty)
                p.quantity -= sell
                acc.cash_balance += sell * px
                if p.quantity <= 0:
                    s.delete(p); pos_by_sym.pop(o.symbol, None)
            else:  # BUY
                cost = qty * px
                if cost > acc.cash_balance:  # 双保险不透支
                    qty = acc.cash_balance / px if px > 0 else Decimal("0")
                    cost = qty * px
                if qty <= 0:
                    continue
                acc.cash_balance -= cost
                p = pos_by_sym.get(o.symbol)
                if p is None:
                    p = orm.MirrorSimPositionModel(mirror_account_id=acc.mirror_account_id,
                                                   symbol=o.symbol, quantity=qty, avg_cost=px)
                    s.add(p); pos_by_sym[o.symbol] = p
                else:
                    new_qty = p.quantity + qty
                    p.avg_cost = (p.quantity * p.avg_cost + qty * px) / new_qty if new_qty > 0 else px
                    p.quantity = new_qty
            s.add(orm.MirrorSimFillModel(
                mirror_account_id=acc.mirror_account_id, symbol=o.symbol, side=o.side,
                quantity=qty, price=px, reason=f"rebalance@{now.isoformat(timespec='seconds')}",
                created_at=now))

    def detail(self, user, *, strategy_id: str) -> dict:
        acc = self._repo.get_by_user_strategy(user.user_id, strategy_id)
        if acc is None:
            raise MirrorSimError("mirror.account_not_found")
        positions = self._repo.list_positions(acc.mirror_account_id)
        prices = _latest_prices([p.symbol for p in positions], acc.market)
        pos_views = []
        hold_mv = 0.0
        for p in positions:
            px = prices.get(p.symbol)
            mv = float(p.quantity) * px if px else None
            if mv is not None:
                hold_mv += mv
            pos_views.append({
                "symbol": p.symbol, "quantity": str(p.quantity), "avg_cost": str(p.avg_cost),
                "current_price": px, "market_value": mv,
                "unrealized_pnl": (mv - float(p.quantity) * float(p.avg_cost)) if mv is not None else None,
            })
        equity = float(acc.cash_balance) + hold_mv
        view = _account_view(acc)
        view.update({
            "positions": pos_views,
            "equity": equity,
            "total_pnl": equity - float(acc.initial_capital),
            "recent_fills": [{
                "symbol": f.symbol, "side": f.side, "quantity": str(f.quantity),
                "price": str(f.price), "reason": f.reason,
                "created_at": f.created_at.isoformat() if f.created_at else None,
            } for f in self._repo.recent_fills(acc.mirror_account_id)],
        })
        return view
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_mirror_sim_service.py -q`
Expected: PASS（全部）

- [ ] **Step 5: 提交**

```bash
git add src/zhice_os_v2/application/services/mirror_sim_service.py tests/test_mirror_sim_service.py
git commit -m "feat(mirror-sim): 再平衡引擎 + 订阅对账冻结/恢复 + 盯市视图"
```

---

## 单元 3：定时任务 + API

### Task 7：scheduler job

**Files:**
- Modify: `src/zhice_os_v2/scheduler_jobs.py`、`src/zhice_os_v2/scheduler.py`
- Test: `tests/test_scheduler_jobs_registration.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_scheduler_jobs_registration.py 追加
def test_mirror_sim_job_registered():
    from zhice_os_v2.scheduler import build_scheduler
    sched = build_scheduler()
    assert sched.get_job("mirror_sim") is not None


def test_mirror_sim_job_sql_only(monkeypatch):
    import zhice_os_v2.scheduler_jobs as jobs
    from zhice_os_v2.config.settings import get_settings
    monkeypatch.setenv("PERSISTENCE_BACKEND", "memory")
    get_settings.cache_clear()
    assert jobs.mirror_sim_job() == {"processed": 0}
    get_settings.cache_clear()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_scheduler_jobs_registration.py -k mirror -q`
Expected: FAIL

- [ ] **Step 3: 加 job + 注册**

`scheduler_jobs.py` 追加（仿 `process_payment_grants_job`）：
```python
def mirror_sim_job() -> dict:
    """跟单模拟盘再平衡（全局，每 5 分钟）：按平台组合权重向各 active 跟单账户收敛。仅 sql 后端。"""
    from zhice_os_v2.config.settings import get_settings
    if get_settings().persistence_backend != "sql":
        return {"processed": 0}
    from zhice_os_v2.application.services.mirror_sim_service import MirrorSimService
    stats = MirrorSimService().sweep()
    if stats.get("orders") or stats.get("frozen") or stats.get("resumed"):
        logger.info("mirror_sim %s", stats)
    return stats
```

`scheduler.py`：`scheduler_jobs` import 块加 `mirror_sim_job,`；在 `process_payment_grants` 注册之后加：
```python
    # 跟单模拟盘再平衡（每 5 分钟，全局）：独立子系统，不碰执行链/推送。
    scheduler.add_job(
        mirror_sim_job,
        IntervalTrigger(minutes=5),
        id="mirror_sim",
        max_instances=1,
        coalesce=True,
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_scheduler_jobs_registration.py -k mirror -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/zhice_os_v2/scheduler_jobs.py src/zhice_os_v2/scheduler.py tests/test_scheduler_jobs_registration.py
git commit -m "feat(mirror-sim): scheduler mirror_sim_job 每5分钟"
```

### Task 8：API `/my/mirror-accounts`

**Files:**
- Create: `src/zhice_os_v2/api/routers/mirror_accounts.py`、`src/zhice_os_v2/api/schemas/mirror_accounts.py`
- Modify: `src/zhice_os_v2/api/main.py`、`src/zhice_os_v2/api/dependencies.py`
- Modify: `frontend/src/i18n/locales/{zh,en}/errors.json`
- Test: `tests/test_mirror_accounts_api.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_mirror_accounts_api.py
"""/my/mirror-accounts：建/列/详情 + 错误码。用隔离 sqlite + patch 服务依赖 + 覆盖鉴权依赖。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from zhice_os_v2.infrastructure.db.base import Base
    import zhice_os_v2.infrastructure.repositories.mirror_sim_repository as repo_mod
    import zhice_os_v2.application.services.mirror_sim_service as svc_mod

    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, expire_on_commit=False)
    for m in (repo_mod, svc_mod):
        monkeypatch.setattr(m, "get_session", lambda: Session(), raising=False)
    monkeypatch.setattr(svc_mod, "_is_subscription_active", lambda uid, sid: sid == "s1")
    monkeypatch.setattr(svc_mod, "_is_observable_builtin", lambda sid: sid == "s1")
    monkeypatch.setattr(svc_mod, "_strategy_market", lambda sid: "CRYPTO")

    from zhice_os_v2.api.main import app
    from zhice_os_v2.api.dependencies import get_current_user, require_active_membership
    from zhice_os_v2.domain.security.user import Role, User

    def _user():
        return User(user_id="u1", username="u1", role=Role.USER)
    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[require_active_membership] = _user
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_create_list_detail(client):
    r = client.post("/api/v1/my/mirror-accounts", json={"strategy_id": "s1", "initial_capital": 10000})
    assert r.status_code == 200, r.text
    assert r.json()["strategy_id"] == "s1"
    r2 = client.post("/api/v1/my/mirror-accounts", json={"strategy_id": "s1", "initial_capital": 10000})
    assert r2.status_code == 409 and r2.json()["detail"]["code"] == "mirror.account_exists"
    r3 = client.post("/api/v1/my/mirror-accounts", json={"strategy_id": "other", "initial_capital": 100})
    assert r3.status_code == 403 and r3.json()["detail"]["code"] == "mirror.subscription_required"
    assert len(client.get("/api/v1/my/mirror-accounts").json()) == 1
    d = client.get("/api/v1/my/mirror-accounts/s1")
    assert d.status_code == 200 and d.json()["status"] == "active" and "positions" in d.json()
    assert client.get("/api/v1/my/mirror-accounts/nope").status_code == 404
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_mirror_accounts_api.py -q`
Expected: FAIL（404 路由未注册）

- [ ] **Step 3: 写 schema + router + 注册 + 依赖 + errors.json**

`src/zhice_os_v2/api/schemas/mirror_accounts.py`：
```python
from pydantic import BaseModel, Field


class CreateMirrorAccountBody(BaseModel):
    strategy_id: str
    initial_capital: float = Field(gt=0)
```

`src/zhice_os_v2/api/routers/mirror_accounts.py`：
```python
"""跟单模拟盘 API（/my/mirror-accounts，owner 隔离）。建账户需活跃会员+活跃订阅。"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException

from zhice_os_v2.api.dependencies import (
    get_current_user, get_mirror_sim_service, require_active_membership, require_roles,
)
from zhice_os_v2.api.schemas.mirror_accounts import CreateMirrorAccountBody
from zhice_os_v2.application.services.mirror_sim_service import MirrorSimError, MirrorSimService
from zhice_os_v2.domain.security.user import Role, User

router = APIRouter(prefix="/my/mirror-accounts", tags=["my-mirror-accounts"],
                   dependencies=[Depends(get_current_user)])
_OWNER_ROLES = (Role.USER, Role.ADMIN)
_STATUS = {
    "mirror.subscription_required": 403, "mirror.not_observable_strategy": 403,
    "mirror.invalid_capital": 422, "mirror.account_exists": 409, "mirror.account_not_found": 404,
}


def _raise(e: MirrorSimError):
    raise HTTPException(status_code=_STATUS.get(e.code, 400), detail={"code": e.code}) from e


@router.post("", dependencies=[Depends(require_active_membership)])
def create_mirror_account(body: CreateMirrorAccountBody,
                          user: User = Depends(require_roles(*_OWNER_ROLES)),
                          service: MirrorSimService = Depends(get_mirror_sim_service)):
    try:
        return service.create_account(user, strategy_id=body.strategy_id,
                                      initial_capital=Decimal(str(body.initial_capital)))
    except MirrorSimError as e:
        _raise(e)


@router.get("")
def list_mirror_accounts(user: User = Depends(get_current_user),
                         service: MirrorSimService = Depends(get_mirror_sim_service)):
    return service.list_accounts(user)


@router.get("/{strategy_id}")
def mirror_account_detail(strategy_id: str, user: User = Depends(get_current_user),
                          service: MirrorSimService = Depends(get_mirror_sim_service)):
    try:
        return service.detail(user, strategy_id=strategy_id)
    except MirrorSimError as e:
        _raise(e)
```

`src/zhice_os_v2/api/dependencies.py` 追加：
```python
def get_mirror_sim_service():
    from zhice_os_v2.application.services.mirror_sim_service import MirrorSimService
    return MirrorSimService()
```

`src/zhice_os_v2/api/main.py`：routers import 块加 `mirror_accounts,`（按现有顺序）；注册区加：
```python
app.include_router(mirror_accounts.router, prefix="/api/v1")
```

`frontend/src/i18n/locales/zh/errors.json` 顶层加 `mirror` 段（若不存在）：
```json
"mirror": {
  "subscription_required": "请先订阅该策略再开模拟盘",
  "not_observable_strategy": "该策略不支持开模拟盘",
  "invalid_capital": "初始本金必须大于 0",
  "account_exists": "该策略已开过模拟盘",
  "account_not_found": "模拟盘账户不存在"
}
```
`frontend/src/i18n/locales/en/errors.json` 对应英文（键同）。

- [ ] **Step 4: 跑测试确认通过 + 校验 JSON**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_mirror_accounts_api.py -q`
Expected: PASS
Run: `cd frontend && python3 -c "import json;json.load(open('src/i18n/locales/zh/errors.json'));json.load(open('src/i18n/locales/en/errors.json'));print('JSON OK')"`

- [ ] **Step 5: 提交**

```bash
git add src/zhice_os_v2/api/routers/mirror_accounts.py src/zhice_os_v2/api/schemas/mirror_accounts.py \
  src/zhice_os_v2/api/main.py src/zhice_os_v2/api/dependencies.py \
  frontend/src/i18n/locales/zh/errors.json frontend/src/i18n/locales/en/errors.json \
  tests/test_mirror_accounts_api.py
git commit -m "feat(mirror-sim): /my/mirror-accounts API + 错误码"
```

---

## 单元 4：前端

### Task 9：mirrorSimApi + i18n

**Files:**
- Create: `frontend/src/services/mirrorSimApi.ts`、`frontend/src/test/mirrorSimApi.test.ts`
- Modify: `frontend/src/i18n/locales/{zh,en}/p_strategy.json`

- [ ] **Step 1: 写失败测试**

```ts
// frontend/src/test/mirrorSimApi.test.ts
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createMirrorAccount, getMirrorAccount } from '../services/mirrorSimApi';

let fetchSpy: ReturnType<typeof vi.spyOn>;
function mockOnce(data: unknown, status = 200) {
  fetchSpy.mockResolvedValueOnce({ ok: status < 300, status,
    json: async () => data, text: async () => JSON.stringify(data) } as unknown as Response);
}
beforeEach(() => { fetchSpy = vi.spyOn(globalThis, 'fetch'); localStorage.setItem('zhice_token', 't'); });
afterEach(() => { fetchSpy.mockRestore(); localStorage.clear(); });

describe('mirrorSimApi', () => {
  it('create POST body strategy_id+initial_capital', async () => {
    mockOnce({ mirror_account_id: 'm1', strategy_id: 's1', status: 'active' });
    await createMirrorAccount('s1', 10000);
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/my/mirror-accounts');
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({ strategy_id: 's1', initial_capital: 10000 });
  });
  it('detail GET by strategy', async () => {
    mockOnce({ strategy_id: 's1', status: 'active', positions: [], equity: 10000, total_pnl: 0 });
    const d = await getMirrorAccount('s1');
    expect((fetchSpy.mock.calls[0] as [string])[0]).toContain('/my/mirror-accounts/s1');
    expect(d.status).toBe('active');
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run src/test/mirrorSimApi.test.ts`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 写 service + i18n**

```ts
// frontend/src/services/mirrorSimApi.ts
import { api } from './api';

export interface MirrorPosition {
  symbol: string; quantity: string; avg_cost: string;
  current_price: number | null; market_value: number | null; unrealized_pnl: number | null;
}
export interface MirrorFill {
  symbol: string; side: string; quantity: string; price: string; reason: string; created_at: string | null;
}
export interface MirrorAccountDetail {
  mirror_account_id: string; strategy_id: string; market: string;
  initial_capital: string; cash_balance: string; status: string; created_at: string | null;
  positions: MirrorPosition[]; equity: number; total_pnl: number; recent_fills: MirrorFill[];
}

const enc = (v: string) => encodeURIComponent(v);

export function createMirrorAccount(strategyId: string, initialCapital: number): Promise<MirrorAccountDetail> {
  return api<MirrorAccountDetail>('/my/mirror-accounts', {
    method: 'POST', body: JSON.stringify({ strategy_id: strategyId, initial_capital: initialCapital }),
  });
}
export function getMirrorAccount(strategyId: string, signal?: AbortSignal): Promise<MirrorAccountDetail> {
  return api<MirrorAccountDetail>(`/my/mirror-accounts/${enc(strategyId)}`, { signal });
}
```

`frontend/src/i18n/locales/zh/p_strategy.json` 的 `strategyDetail` 段加：
```json
"mirrorOpen": "开模拟盘",
"mirrorTitle": "我的模拟盘",
"mirrorCapital": "初始本金",
"mirrorEquity": "总权益",
"mirrorCash": "现金",
"mirrorPnl": "总盈亏",
"mirrorFrozen": "订阅已失效，模拟盘已冻结，重新订阅后自动恢复跟单",
"mirrorColSymbol": "标的",
"mirrorColQty": "数量",
"mirrorColAvg": "均价",
"mirrorColPrice": "现价",
"mirrorColMv": "市值",
"mirrorColUpnl": "未实现盈亏",
"mirrorEmpty": "暂无模拟持仓（跟随平台新开仓后自动建仓）",
"mirrorCreated": "模拟盘已创建",
"mirrorCapitalRequired": "请输入初始本金"
```
`frontend/src/i18n/locales/en/p_strategy.json` 对应英文（键同）。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run src/test/mirrorSimApi.test.ts`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git -C .. add frontend/src/services/mirrorSimApi.ts frontend/src/test/mirrorSimApi.test.ts \
  frontend/src/i18n/locales/zh/p_strategy.json frontend/src/i18n/locales/en/p_strategy.json
git -C .. commit -m "feat(mirror-sim): 前端 mirrorSimApi + i18n"
```

### Task 10：StrategyDetailPage 开模拟盘 + 我的模拟盘

**Files:**
- Modify: `frontend/src/pages/StrategyDetailPage.tsx`
- Test: `frontend/src/test/StrategyDetailMirrorSim.test.tsx`

- [ ] **Step 1: 写失败测试**

```tsx
// frontend/src/test/StrategyDetailMirrorSim.test.tsx
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../services/mirrorSimApi', () => ({
  createMirrorAccount: vi.fn(), getMirrorAccount: vi.fn(),
}));
vi.mock('../services/myStrategiesApi', () => ({ listCatalog: vi.fn().mockResolvedValue([
  { strategy_id: 's1', strategy_name: '虚拟币均值回归', market: 'CRYPTO', owner_user_id: null,
    versions: [{ strategy_version_id: 'sv1' }] }]) }));
vi.mock('../services/billingApi', () => ({ listSubscriptions: vi.fn().mockResolvedValue({
  items: [{ strategy_id: 's1', active: true, effective_status: 'active' }] }) }));
vi.mock('../services/subscriptionObservationApi', () => ({
  getSubscriptionPositions: vi.fn().mockResolvedValue([]),
  getSubscriptionTrades: vi.fn().mockResolvedValue([]),
}));
vi.mock('../services/api', () => ({ api: vi.fn().mockResolvedValue(
  { strategy_version_id: 'sv1', market: 'CRYPTO', strategy_name: '虚拟币均值回归' }) }));

import '../i18n';
import StrategyDetailPage from '../pages/StrategyDetailPage';
import { createMirrorAccount, getMirrorAccount } from '../services/mirrorSimApi';

const mCreate = vi.mocked(createMirrorAccount);
const mGet = vi.mocked(getMirrorAccount);

beforeEach(() => { vi.clearAllMocks(); localStorage.setItem('zhice_token', 't'); localStorage.setItem('zhice_role', 'USER'); });
afterEach(() => vi.restoreAllMocks());

describe('StrategyDetailPage 跟单模拟盘', () => {
  it('无账户显示"开模拟盘"，创建后展示我的模拟盘', async () => {
    mGet.mockRejectedValueOnce({ status: 404 });
    mCreate.mockResolvedValue({ mirror_account_id: 'm1', strategy_id: 's1', market: 'CRYPTO',
      initial_capital: '10000', cash_balance: '10000', status: 'active', created_at: null,
      positions: [], equity: 10000, total_pnl: 0, recent_fills: [] } as never);
    render(<MemoryRouter><StrategyDetailPage id="sv1" /></MemoryRouter>);
    const btn = await screen.findByRole('button', { name: /开\s*模拟盘|开模拟盘/ });
    fireEvent.click(btn);
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByRole('spinbutton'), { target: { value: '10000' } });
    fireEvent.click(within(dialog).getByRole('button', { name: /确\s*定|OK|开\s*模拟盘|开模拟盘/ }));
    await waitFor(() => expect(mCreate).toHaveBeenCalledWith('s1', 10000));
    expect(await screen.findByText('我的模拟盘')).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run src/test/StrategyDetailMirrorSim.test.tsx`
Expected: FAIL（无开模拟盘按钮）

- [ ] **Step 3: 改 StrategyDetailPage**

在 import 区加（若未导入）：
```tsx
import { InputNumber } from 'antd';
import { createMirrorAccount, getMirrorAccount, type MirrorAccountDetail, type MirrorPosition } from '../services/mirrorSimApi';
```
在观察区 state 附近加：
```tsx
const [mirror, setMirror] = useState<MirrorAccountDetail | null>(null);
const [mirrorOpen, setMirrorOpen] = useState(false);
const [mirrorCapital, setMirrorCapital] = useState<number>(10000);
const [mirrorBusy, setMirrorBusy] = useState(false);
```
加拉取 effect + 提交函数（`obsStrategyId` 生效时拉，404→null 显示开户按钮）：
```tsx
useEffect(() => {
  if (!obsStrategyId) { setMirror(null); return; }
  const ac = new AbortController();
  getMirrorAccount(obsStrategyId, ac.signal).then(setMirror).catch(() => setMirror(null));
  return () => ac.abort();
}, [obsStrategyId]);

async function submitMirror() {
  if (!obsStrategyId) return;
  if (!mirrorCapital || mirrorCapital <= 0) { message.error(t('strategyDetail.mirrorCapitalRequired')); return; }
  setMirrorBusy(true);
  try {
    const created = await createMirrorAccount(obsStrategyId, mirrorCapital);
    message.success(t('strategyDetail.mirrorCreated'));
    setMirror(created); setMirrorOpen(false);
  } catch { /* api 层已 toast */ } finally { setMirrorBusy(false); }
}
```
在观察区渲染块（`{obsStrategyId && (…)}`）内、两张平台 Card 之后加：
```tsx
<Card size="small" title={t('strategyDetail.mirrorTitle')} style={{ marginTop: 16 }}
  extra={!mirror ? (
    <Button type="primary" size="small" onClick={() => setMirrorOpen(true)}>
      {t('strategyDetail.mirrorOpen')}
    </Button>) : null}>
  {!mirror ? (
    <Typography.Text type="secondary">{t('strategyDetail.mirrorEmpty')}</Typography.Text>
  ) : (
    <>
      {mirror.status === 'frozen' && (
        <Typography.Text type="warning">{t('strategyDetail.mirrorFrozen')}</Typography.Text>)}
      <Space size={24} style={{ margin: '8px 0' }} wrap>
        <span>{t('strategyDetail.mirrorEquity')}: {mirror.equity.toFixed(2)}</span>
        <span>{t('strategyDetail.mirrorCash')}: {Number(mirror.cash_balance).toFixed(2)}</span>
        <span>{t('strategyDetail.mirrorPnl')}: {mirror.total_pnl.toFixed(2)}</span>
      </Space>
      <Table<MirrorPosition> size="small" rowKey="symbol" dataSource={mirror.positions}
        pagination={false} locale={{ emptyText: t('strategyDetail.mirrorEmpty') }}
        columns={[
          { title: t('strategyDetail.mirrorColSymbol'), dataIndex: 'symbol' },
          { title: t('strategyDetail.mirrorColQty'), dataIndex: 'quantity' },
          { title: t('strategyDetail.mirrorColAvg'), dataIndex: 'avg_cost' },
          { title: t('strategyDetail.mirrorColPrice'), dataIndex: 'current_price' },
          { title: t('strategyDetail.mirrorColMv'), dataIndex: 'market_value' },
          { title: t('strategyDetail.mirrorColUpnl'), dataIndex: 'unrealized_pnl' },
        ]} />
    </>
  )}
</Card>
<Modal title={t('strategyDetail.mirrorOpen')} open={mirrorOpen} confirmLoading={mirrorBusy}
  onOk={submitMirror} onCancel={() => setMirrorOpen(false)}>
  <span>{t('strategyDetail.mirrorCapital')}：</span>
  <InputNumber min={1} value={mirrorCapital} onChange={(v) => setMirrorCapital(Number(v) || 0)}
    style={{ width: 200 }} />
</Modal>
```
（确保 `Card/Button/Table/Modal/Space/Typography/message/InputNumber` 已 import；`Table`/`Card`/`Space` 详情页已在用。）

- [ ] **Step 4: 跑测试 + tsc**

Run: `cd frontend && npx vitest run src/test/StrategyDetailMirrorSim.test.tsx && npx tsc -b`
Expected: PASS + tsc 干净

- [ ] **Step 5: 提交**

```bash
git -C .. add frontend/src/pages/StrategyDetailPage.tsx frontend/src/test/StrategyDetailMirrorSim.test.tsx
git -C .. commit -m "feat(mirror-sim): 详情页开模拟盘 + 我的模拟盘区"
```

---

## 单元 5：推送隔离专项 + real_pg + 门禁

### Task 11：推送隔离专项测试

**Files:**
- Test: `tests/test_mirror_sim_push_isolation.py`

- [ ] **Step 1: 写测试**（静态断言：mirror 子系统源码不触碰推送函数）

```python
# tests/test_mirror_sim_push_isolation.py
"""硬约束：跟单子系统绝不调用 record_fill/route_subscription_signal/route_fill/notify_fill。"""
from __future__ import annotations

from pathlib import Path

_FORBIDDEN = ("route_subscription_signal", "route_fill(", "notify_fill", "record_fill")


def test_mirror_service_and_repo_do_not_touch_push():
    root = Path(__file__).resolve().parents[1]
    svc = (root / "src/zhice_os_v2/application/services/mirror_sim_service.py").read_text("utf-8")
    repo = (root / "src/zhice_os_v2/infrastructure/repositories/mirror_sim_repository.py").read_text("utf-8")
    for token in _FORBIDDEN:
        assert token not in svc, f"mirror_sim_service 不应出现 {token}"
        assert token not in repo, f"mirror_sim_repository 不应出现 {token}"


def test_mirror_job_isolated_from_push():
    root = Path(__file__).resolve().parents[1]
    jobs = (root / "src/zhice_os_v2/scheduler_jobs.py").read_text("utf-8")
    start = jobs.index("def mirror_sim_job")
    nxt = jobs.find("\ndef ", start + 1)
    body = jobs[start: nxt if nxt != -1 else len(jobs)]
    for token in ("route_subscription_signal", "notify_fill", "record_fill"):
        assert token not in body
```

- [ ] **Step 2: 跑测试确认通过**（实现已隔离，应直接绿）

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_mirror_sim_push_isolation.py -q`
Expected: PASS（若失败说明实现引入推送调用 → 移除）

- [ ] **Step 3: 提交**

```bash
git add tests/test_mirror_sim_push_isolation.py
git commit -m "test(mirror-sim): 推送隔离专项"
```

### Task 12：real_pg sweep + 冻结

**Files:**
- Test: `tests/test_mirror_sim_concurrency_pg.py`

- [ ] **Step 1: 写 real_pg 测试**

```python
# tests/test_mirror_sim_concurrency_pg.py
"""real_pg：sweep 对真实平台开仓建仓 + 撤订冻结（需 REAL_PG=1 + PG 已迁到 0032）。"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

pytestmark = pytest.mark.real_pg


def _pg_ok() -> bool:
    try:
        from sqlalchemy import text
        from zhice_os_v2.infrastructure.db.session import get_session
        s = get_session()
        try:
            s.execute(text("select 1")); return True
        finally:
            s.close()
    except Exception:
        return False


@pytest.fixture()
def pg():
    if not (os.getenv("REAL_PG") == "1" and _pg_ok()):
        pytest.skip("需 REAL_PG=1 且真 PG 到 0032")
    from sqlalchemy import text
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session
    sfx = datetime.now(UTC).strftime("%H%M%S%f")
    uid, sid, svid = f"zm_u_{sfx}", f"zm_s_{sfx}", f"zm_sv_{sfx}"
    t0 = datetime(2026, 9, 1, tzinfo=UTC)
    s = get_session()
    try:
        s.add(orm.UserModel(user_id=uid, username=uid, password_hash="x", role="USER",
                            is_active=True, locale="zh", created_at=t0))
        s.add(orm.StrategyModel(strategy_id=sid, strategy_name=sid, market="CRYPTO", status="ACTIVE",
                                owner_user_id=None, visibility="PUBLIC", publication_status="PUBLISHED",
                                created_at=t0, updated_at=t0))
        s.add(orm.StrategySubscriptionModel(user_id=uid, strategy_id=sid, status="active",
                                            is_permanent=True, version=1, created_at=t0, updated_at=t0))
        s.flush()
        s.add(orm.StrategyVersionModel(strategy_version_id=svid, strategy_id=sid, version_no="1",
                                       market="CRYPTO", status="ACTIVE", spec={}, created_at=t0))
        s.add(orm.PositionModel(position_id=f"p_{sfx}", strategy_version_id=svid, market="CRYPTO",
                                ts_code="BTCUSDT", direction="LONG", open_qty=Decimal("2"),
                                open_price=Decimal("100"), status="OPEN", opened_at=t0 + timedelta(days=10),
                                user_strategy_id=f"ustrat-legacy-{svid}:CRYPTO"))
        s.commit()
    finally:
        s.close()
    yield {"uid": uid, "sid": sid, "svid": svid}
    c = get_session()
    try:
        c.execute(text("DELETE FROM v2_mirror_sim_fill WHERE mirror_account_id IN "
                       "(SELECT mirror_account_id FROM v2_mirror_sim_account WHERE user_id=:u)"), {"u": uid})
        c.execute(text("DELETE FROM v2_mirror_sim_position WHERE mirror_account_id IN "
                       "(SELECT mirror_account_id FROM v2_mirror_sim_account WHERE user_id=:u)"), {"u": uid})
        c.execute(text("DELETE FROM v2_mirror_sim_account WHERE user_id=:u"), {"u": uid})
        c.execute(text("DELETE FROM v2_position WHERE strategy_version_id=:v"), {"v": svid})
        c.execute(text("DELETE FROM v2_strategy_version WHERE strategy_version_id=:v"), {"v": svid})
        c.execute(text("DELETE FROM v2_strategy_subscription WHERE user_id=:u"), {"u": uid})
        c.execute(text("DELETE FROM v2_strategy WHERE strategy_id=:s"), {"s": sid})
        c.execute(text("DELETE FROM v2_user WHERE user_id=:u"), {"u": uid})
        c.commit()
    finally:
        c.close()


def test_sweep_builds_position_then_freeze(pg, monkeypatch):
    import zhice_os_v2.application.services.mirror_sim_service as svc_mod
    monkeypatch.setattr(svc_mod, "_latest_prices", lambda syms, market: {"BTCUSDT": 100.0})
    monkeypatch.setattr(svc_mod, "_now", lambda: datetime(2026, 9, 6, tzinfo=UTC))

    class U:
        user_id = pg["uid"]
    svc = svc_mod.MirrorSimService()
    svc.create_account(U(), strategy_id=pg["sid"], initial_capital=Decimal("10000"))
    svc.sweep()
    d = svc.detail(U(), strategy_id=pg["sid"])
    pos = {p["symbol"]: p for p in d["positions"]}
    assert "BTCUSDT" in pos and float(pos["BTCUSDT"]["quantity"]) > 0

    from sqlalchemy import text
    from zhice_os_v2.infrastructure.db.session import get_session
    s = get_session()
    s.execute(text("UPDATE v2_strategy_subscription SET status='revoked' WHERE user_id=:u"), {"u": pg["uid"]})
    s.commit(); s.close()
    svc.sweep()
    assert svc.detail(U(), strategy_id=pg["sid"])["status"] == "frozen"
```

- [ ] **Step 2: 建临时库 + 跑**

```bash
createdb -O zhice zhice_mirror_test
DB_MAIN_URL=postgresql://zhice:zhice_pass@127.0.0.1:5432/zhice_mirror_test PERSISTENCE_BACKEND=sql PYTHONPATH=src .venv/bin/alembic upgrade 0032
REAL_PG=1 DB_MAIN_URL=postgresql://zhice:zhice_pass@127.0.0.1:5432/zhice_mirror_test PERSISTENCE_BACKEND=sql PYTHONPATH=src .venv/bin/python -m pytest -o addopts="" -m real_pg tests/test_mirror_sim_concurrency_pg.py -q
dropdb zhice_mirror_test
```
Expected: PASS（1 passed）

- [ ] **Step 3: 提交**

```bash
git add tests/test_mirror_sim_concurrency_pg.py
git commit -m "test(mirror-sim): real_pg sweep 建仓 + 撤订冻结"
```

### Task 13：全量门禁总核

- [ ] **Step 1: 后端门禁**

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
.venv/bin/mypy src/zhice_os_v2/application/services/mirror_sim_service.py \
  src/zhice_os_v2/infrastructure/repositories/mirror_sim_repository.py \
  src/zhice_os_v2/api/routers/mirror_accounts.py
PYTHONPATH=src .venv/bin/alembic heads    # 单一 head=0032
```
Expected: 全绿；mypy 本特性文件无新增错误。

- [ ] **Step 2: 前端门禁**

```bash
cd frontend && npx vitest run && npx tsc -b && npx vite build
```
Expected: 全绿。

- [ ] **Step 3: 收尾提交**（如有零散改动）

```bash
git add -A && git commit -m "chore(mirror-sim): 门禁总核收尾" || echo "无待提交"
```

---

## 自检（写完计划的复核）

- **Spec 覆盖**：§3 数据模型→Task1-2；§4 引擎→Task3/6；§5 API→Task8；§6 生命周期→Task6(`_reconcile_subscriptions`)；§7 前端→Task9-10；§8 推送隔离→Task11；§9 边界(现金封顶/缺价/除零/阈值)→Task3/6；§10 测试→各 Task；§11 门禁→Task13。forward-only 锚点由 `account.created_at` vs `position.opened_at` 落地（Task6），spec §3 的 `created_fill_cursor` 简化为用 `created_at`，语义等价，已在 Architecture 注明。
- **占位符**：无 TBD/TODO；每步含真实代码/命令/期望输出。
- **类型一致**：`compute_target_mv/rebalance_orders/RebalanceOrder`（Task3）↔ 服务调用（Task6）签名一致；`MirrorSimError.code` 码集（Task5/8）↔ errors.json（Task8）↔ `_STATUS` 映射一致；`createMirrorAccount/getMirrorAccount` + `MirrorAccountDetail/MirrorPosition`（Task9）↔ 详情页（Task10）↔ 后端 `detail()` 返回字段（Task6）一致。

## 备注

- **未部署**：本计划仅本地实现+验证。部署到 207（Docker Compose，迁移 0032 加表不碰既有数据，scheduler 注册 mirror_sim_job，两市场 US,CRYPTO 不变）需你另行明确授权。
- **执行顺序**：单元 1→5 线性；单元 4 前端可与单元 3 后端并行（接口契约已在 Task8/Task9 定死）。
