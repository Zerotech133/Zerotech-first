"""user account (v2_user_account)

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-07

新建 v2_user_account 表：持久化用户账户（独立资金/风控账本，只属于一个用户，
固定一个市场与本位币）。独立 revision，down_revision=0017（绝不追加进 0016/0017）。
upgrade/downgrade 对称可逆。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "v2_user_account",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("v2_user.user_id"),
            nullable=False,
        ),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column("currency", sa.String(16), nullable=False),
        sa.Column("account_type", sa.String(24), nullable=False),
        sa.Column("initial_capital", sa.Numeric(24, 8), nullable=False),
        sa.Column("cash_balance", sa.Numeric(24, 8), nullable=False),
        sa.Column(
            "reserved_cash", sa.Numeric(24, 8), nullable=False, server_default="0"
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "account_type IN ('SHARED_PORTFOLIO','STRATEGY_DEDICATED')",
            name="ck_v2_user_account_type",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','FROZEN','CLOSED')",
            name="ck_v2_user_account_status",
        ),
        sa.CheckConstraint(
            "initial_capital > 0", name="ck_v2_user_account_initial_capital"
        ),
        sa.CheckConstraint("cash_balance >= 0", name="ck_v2_user_account_cash_balance"),
        sa.CheckConstraint(
            "reserved_cash >= 0", name="ck_v2_user_account_reserved_cash"
        ),
    )
    op.create_index(
        "ix_v2_user_account_account_id", "v2_user_account", ["account_id"]
    )
    op.create_index("ix_v2_user_account_user_id", "v2_user_account", ["user_id"])
    op.create_index(
        "ix_v2_user_account_user_market", "v2_user_account", ["user_id", "market"]
    )


def downgrade() -> None:
    op.drop_index("ix_v2_user_account_user_market", table_name="v2_user_account")
    op.drop_index("ix_v2_user_account_user_id", table_name="v2_user_account")
    op.drop_index("ix_v2_user_account_account_id", table_name="v2_user_account")
    op.drop_table("v2_user_account")
