"""user strategy instance (v2_user_strategy_instance)

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-07

新建 v2_user_strategy_instance 表：用户策略运行实例（某用户在某账户上运行某具体策略版本
的边界）。独立 revision，down_revision=0018（绝不追加进旧迁移）。upgrade/downgrade 对称。
部分唯一索引 uq_v2_user_strategy_active_account_version（enabled IS TRUE）保证同一账户
同一版本最多一个启用实例。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "v2_user_strategy_instance",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_strategy_id", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("v2_user.user_id"),
            nullable=False,
        ),
        sa.Column(
            "strategy_version_id",
            sa.String(64),
            sa.ForeignKey("v2_strategy_version.strategy_version_id"),
            nullable=False,
        ),
        sa.Column(
            "account_id",
            sa.String(64),
            sa.ForeignKey("v2_user_account.account_id"),
            nullable=False,
        ),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "auto_execute",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "execution_mode",
            sa.String(16),
            nullable=False,
            server_default="MANUAL",
        ),
        sa.Column("capital_quota", sa.Numeric(24, 8), nullable=True),
        sa.Column(
            "max_position_ratio",
            sa.Numeric(12, 6),
            nullable=False,
            server_default="0.1",
        ),
        sa.Column(
            "risk_parameters",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "strategy_parameters",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "notification_preferences",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "upgrade_available_version_id",
            sa.String(64),
            sa.ForeignKey("v2_strategy_version.strategy_version_id"),
            nullable=True,
        ),
        sa.Column(
            "pending_upgrade_version_id",
            sa.String(64),
            sa.ForeignKey("v2_strategy_version.strategy_version_id"),
            nullable=True,
        ),
        sa.Column("upgrade_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "execution_mode IN ('MANUAL','SEMI_AUTO','FULL_AUTO')",
            name="ck_v2_user_strategy_execution_mode",
        ),
        sa.CheckConstraint(
            "max_position_ratio > 0 AND max_position_ratio <= 1",
            name="ck_v2_user_strategy_max_position_ratio",
        ),
    )
    op.create_index(
        "ix_v2_user_strategy_user_strategy_id",
        "v2_user_strategy_instance",
        ["user_strategy_id"],
    )
    op.create_index(
        "ix_v2_user_strategy_user_enabled",
        "v2_user_strategy_instance",
        ["user_id", "enabled"],
    )
    op.create_index(
        "ix_v2_user_strategy_version",
        "v2_user_strategy_instance",
        ["strategy_version_id"],
    )
    op.create_index(
        "ix_v2_user_strategy_account",
        "v2_user_strategy_instance",
        ["account_id"],
    )
    # 同一账户同一版本最多一个启用实例（PostgreSQL 部分唯一索引）。
    op.create_index(
        "uq_v2_user_strategy_active_account_version",
        "v2_user_strategy_instance",
        ["account_id", "strategy_version_id"],
        unique=True,
        postgresql_where=sa.text("enabled IS TRUE"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_v2_user_strategy_active_account_version",
        table_name="v2_user_strategy_instance",
    )
    op.drop_index(
        "ix_v2_user_strategy_account", table_name="v2_user_strategy_instance"
    )
    op.drop_index(
        "ix_v2_user_strategy_version", table_name="v2_user_strategy_instance"
    )
    op.drop_index(
        "ix_v2_user_strategy_user_enabled", table_name="v2_user_strategy_instance"
    )
    op.drop_index(
        "ix_v2_user_strategy_user_strategy_id",
        table_name="v2_user_strategy_instance",
    )
    op.drop_table("v2_user_strategy_instance")
