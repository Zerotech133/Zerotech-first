"""trade runtime ownership columns (Phase B Task 1)

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-07

多用户交易执行隔离 Phase B Task 1：给交易链五张表各加两个 nullable 运行时归属列
（user_strategy_id、account_id），并建索引 + nullable 外键。纯 additive：新列全 nullable，
legacy 写入路径不设置它们（恒 NULL），不改变任何现有交易/执行行为。

- user_strategy_id -> v2_user_strategy_instance.user_strategy_id（FK，nullable）
- account_id       -> v2_user_account.account_id（FK，nullable）

只加在 v2_trade_plan / v2_approval_request / v2_order_intent / v2_position / v2_replay。
v2_order_request / v2_execution_record / v2_fill_record 不加——它们经 order_intent 解析
归属（Task 2 的 OwnershipResolver）。

独立 revision，down_revision=0020（绝不追加进旧迁移）。upgrade/downgrade 对称可逆。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 加归属列的交易链表（不含 order_request/execution_record/fill_record）。
_TABLES: tuple[str, ...] = (
    "v2_trade_plan",
    "v2_approval_request",
    "v2_order_intent",
    "v2_position",
    "v2_replay",
)


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column(
                "user_strategy_id",
                sa.String(64),
                sa.ForeignKey(
                    "v2_user_strategy_instance.user_strategy_id",
                    name=f"fk_{table}_user_strategy_id",
                ),
                nullable=True,
            ),
        )
        op.add_column(
            table,
            sa.Column(
                "account_id",
                sa.String(64),
                sa.ForeignKey(
                    "v2_user_account.account_id",
                    name=f"fk_{table}_account_id",
                ),
                nullable=True,
            ),
        )
        op.create_index(
            f"ix_{table}_user_strategy_id", table, ["user_strategy_id"]
        )
        op.create_index(f"ix_{table}_account_id", table, ["account_id"])


def downgrade() -> None:
    for table in _TABLES:
        op.drop_index(f"ix_{table}_account_id", table_name=table)
        op.drop_index(f"ix_{table}_user_strategy_id", table_name=table)
        op.drop_constraint(f"fk_{table}_account_id", table, type_="foreignkey")
        op.drop_constraint(
            f"fk_{table}_user_strategy_id", table, type_="foreignkey"
        )
        op.drop_column(table, "account_id")
        op.drop_column(table, "user_strategy_id")
