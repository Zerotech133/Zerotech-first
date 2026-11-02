"""ACAD1 逐票投研卡 + 审核/交接盯盘（0038）。

一只池内股票一条投研卡；不改策略、不写交易。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "v2_stock_research",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("pool_date", sa.Date(), nullable=False),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False, server_default=""),
        sa.Column("direction", sa.String(8), nullable=False, server_default="中性"),
        sa.Column("score", sa.Numeric(5, 1), nullable=True),
        sa.Column("rating", sa.String(4), nullable=False, server_default="C"),
        sa.Column("thesis", sa.Text(), nullable=False, server_default=""),
        sa.Column("catalysts", sa.Text(), nullable=False, server_default=""),
        sa.Column("risks", sa.Text(), nullable=False, server_default=""),
        sa.Column("buy_zone_low", sa.Numeric(12, 3), nullable=True),
        sa.Column("buy_zone_high", sa.Numeric(12, 3), nullable=True),
        sa.Column("target_price", sa.Numeric(12, 3), nullable=True),
        sa.Column("stop_price", sa.Numeric(12, 3), nullable=True),
        sa.Column("invalidation", sa.Text(), nullable=False, server_default=""),
        sa.Column("executable", sa.String(16), nullable=False, server_default="观察"),
        sa.Column("exec_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("reviewer", sa.String(32), nullable=False, server_default=""),
        sa.Column("review_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("handoff_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_date", sa.Date(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("pool_date", "strategy_id", "code",
                            name="uq_v2_stock_research_item"),
        sa.Index("ix_v2_stock_research_date", "pool_date"),
    )


def downgrade() -> None:
    op.drop_table("v2_stock_research")
