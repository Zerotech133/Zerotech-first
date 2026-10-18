"""billing entitlements (P1 权益地基)

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-08

商业化改造 P1：新增带有效期的权益层，与永久 role 正交叠加。

- v2_membership：每用户一行会员资格。tier=free（注册默认，valid_until=NULL 永久）
  或 member（付费/发放，valid_until=到期时刻）。status=active/expired 供展示；
  真实有效性以 valid_until>=now 懒判定为准（tier=member 且过期即失效）。
- v2_strategy_subscription：用户对某内置策略的按月订阅。valid_until>=now 即有效，
  用于向订阅者扇出该策略入场/出场信号。unique(user_id, strategy_id)。

规范同 0018：v2_ 前缀、FK→v2_user.user_id、DateTime(timezone=True)、显式索引、
CheckConstraint 约束枚举、upgrade/downgrade 对称可逆。定价配置不建表，存 v2_app_state。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "v2_membership",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("v2_user.user_id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("tier", sa.String(16), nullable=False, server_default="free"),
        # NULL=永久（free）；member 为到期时刻。
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("tier IN ('free','member')", name="ck_v2_membership_tier"),
        sa.CheckConstraint(
            "status IN ('active','expired')", name="ck_v2_membership_status"
        ),
    )
    op.create_index("ix_v2_membership_user_id", "v2_membership", ["user_id"])

    op.create_table(
        "v2_strategy_subscription",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("v2_user.user_id"),
            nullable=False,
        ),
        sa.Column(
            "strategy_id",
            sa.String(64),
            sa.ForeignKey("v2_strategy.strategy_id"),
            nullable=False,
        ),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "user_id", "strategy_id", name="uq_v2_strategy_subscription_user_strategy"
        ),
        sa.CheckConstraint(
            "status IN ('active','expired')",
            name="ck_v2_strategy_subscription_status",
        ),
    )
    op.create_index(
        "ix_v2_strategy_subscription_user_id",
        "v2_strategy_subscription",
        ["user_id"],
    )
    op.create_index(
        "ix_v2_strategy_subscription_strategy_id",
        "v2_strategy_subscription",
        ["strategy_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_v2_strategy_subscription_strategy_id",
        table_name="v2_strategy_subscription",
    )
    op.drop_index(
        "ix_v2_strategy_subscription_user_id", table_name="v2_strategy_subscription"
    )
    op.drop_table("v2_strategy_subscription")
    op.drop_index("ix_v2_membership_user_id", table_name="v2_membership")
    op.drop_table("v2_membership")
