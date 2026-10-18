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
