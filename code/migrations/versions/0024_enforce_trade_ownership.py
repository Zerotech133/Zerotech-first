"""enforce NOT NULL trade ownership (Phase D Task 9)

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-07

多用户改造 Phase D Task 9：交易链五表归属列收紧为 NOT NULL。**安全铁律：仅在
回填完成且 strict verifier 通过后执行**——upgrade 先前置校验五表无 NULL 归属
（等价 verify_multi_user_ownership.py 的 null_owner strict 检查），任一表
user_strategy_id 或 account_id 存在 NULL 即 RuntimeError 拒绝升级，绝不带着
NULL 强设 NOT NULL（那只会得到裸 PG 错误）；错误消息列出每表 NULL 计数并指引
先跑回填脚本 + strict 校验。

- 五表：v2_trade_plan / v2_approval_request / v2_order_intent / v2_position /
  v2_replay；每表 user_strategy_id、account_id 两列 SET NOT NULL。
- **不加 FK、不建索引**：0021_trade_runtime_ownership 已建
  fk_{table}_user_strategy_id / fk_{table}_account_id 外键与
  ix_{table}_user_strategy_id / ix_{table}_account_id 单列索引，此处不重复。
  request/execution/fill 经 intent 链解析归属，仍无归属列（Phase B 既定设计）。
- 纯约束收紧，不动任何数据；downgrade 对称放开（nullable=True），数据不动。
- PG 的 SET NOT NULL 需全表扫描并持 ACCESS EXCLUSIVE 锁——dev 库量级可接受；
  159 生产大库操作前须维护窗口 + pg_dump（见 docs/runbooks/multi-user-rollout.md，
  Task 10 落笔）。

upgrade() 的前置校验抽成 `_assert_no_null_ownership(bind)` 独立函数：
tests/test_migration_0024.py 传隔离 sqlite engine 直接调用测试（有 NULL 拒绝 /
无 NULL 通过）；SET NOT NULL DDL 本体由 dev 库 rehearsal（backup→backfill→
verify strict→0024→verify strict→round-trip）验证。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 收紧归属的交易链五表（与 0021/回填脚本/verifier 同表集）。
_TABLES: tuple[str, ...] = (
    "v2_trade_plan",
    "v2_approval_request",
    "v2_order_intent",
    "v2_position",
    "v2_replay",
)
_COLUMNS: tuple[str, ...] = ("user_strategy_id", "account_id")

# NULL 拒绝时的修复指引（与实际脚本路径严格一致）。
_BACKFILL_HINT = (
    "resolve NULL ownership first: "
    "uv run python scripts/backfill_multi_user_ownership.py --apply, then "
    "uv run python scripts/verify_multi_user_ownership.py --strict must pass"
)


def _null_ownership_counts(bind: Connection) -> dict[str, int]:
    """五表归属列 NULL 行计数（user_strategy_id 或 account_id 任一为 NULL 即计）。"""
    counts: dict[str, int] = {}
    for table in _TABLES:
        n = bind.execute(
            sa.text(
                f"SELECT count(*) FROM {table} "  # noqa: S608 — 表名来自白名单常量
                "WHERE user_strategy_id IS NULL OR account_id IS NULL"
            )
        ).scalar_one()
        counts[table] = int(n)
    return counts


def _assert_no_null_ownership(bind: Connection) -> None:
    """0024 升级门禁：任一表存在 NULL 归属行即 RuntimeError，绝不发 DDL。"""
    bad = {t: n for t, n in _null_ownership_counts(bind).items() if n > 0}
    if bad:
        detail = ", ".join(f"{table}={count}" for table, count in sorted(bad.items()))
        raise RuntimeError(
            f"aborting revision 0024: NULL ownership rows remain "
            f"[{detail}]; {_BACKFILL_HINT}"
        )


def upgrade() -> None:
    _assert_no_null_ownership(op.get_bind())
    for table in _TABLES:
        for column in _COLUMNS:
            op.alter_column(table, column, nullable=False)


def downgrade() -> None:
    # 对称放开：只改 nullable，绝不动数据。
    for table in _TABLES:
        for column in _COLUMNS:
            op.alter_column(table, column, nullable=True)
