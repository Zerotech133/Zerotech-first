"""策略模拟盘 + ACAD1 因子验证台（0042）。"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "v2_sim_account",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("strategy_id", sa.String(64), nullable=False, unique=True),
        sa.Column("initial_capital", sa.Numeric(18, 2), nullable=False, server_default="1000000"),
        sa.Column("cash", sa.Numeric(18, 2), nullable=False, server_default="1000000"),
        sa.Column("base_position_pct", sa.Numeric(5, 1), nullable=False, server_default="40"),
        sa.Column("max_position_pct", sa.Numeric(5, 1), nullable=False, server_default="100"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_table(
        "v2_sim_order",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False, server_default=""),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("price", sa.Numeric(12, 3), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Index("ix_v2_sim_order_strategy", "strategy_id", "trade_date"),
    )
    op.create_table(
        "v2_sim_position",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False, server_default=""),
        sa.Column("qty", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("avg_cost", sa.Numeric(12, 3), nullable=False, server_default="0"),
        sa.Column("last_price", sa.Numeric(12, 3), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("strategy_id", "code", name="uq_v2_sim_position"),
    )
    op.create_table(
        "v2_factor_candidate",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("source", sa.String(128), nullable=False, server_default=""),
        sa.Column("logic", sa.Text(), nullable=False, server_default=""),
        sa.Column("data_ref", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="candidate"),
        sa.Column("created_by", sa.String(32), nullable=False, server_default="ACAD1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_table(
        "v2_factor_consensus",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("factor_id", sa.BigInteger(), nullable=False),
        sa.Column("strategist", sa.String(32), nullable=False),
        sa.Column("stance", sa.String(16), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_table(
        "v2_factor_paper_trade",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("factor_id", sa.BigInteger(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False, server_default=""),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("price", sa.Numeric(12, 3), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("pnl", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )


def downgrade() -> None:
    for t in ("v2_factor_paper_trade", "v2_factor_consensus", "v2_factor_candidate",
              "v2_sim_position", "v2_sim_order", "v2_sim_account"):
        op.drop_table(t)
