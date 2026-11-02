"""ACAD1 research pool + review tables (0036).

Additive only, same DB as sidecar/daily. The pool is ACAD1-selected research
candidates, never the platform candidate/trade pool.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "v2_acad1_pool",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("pool_date", sa.Date(), nullable=False),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False, server_default=""),
        sa.Column("market", sa.String(16), nullable=False, server_default="CN"),
        sa.Column("signal", sa.String(32), nullable=False, server_default=""),
        sa.Column("score", sa.Numeric(12, 4), nullable=True),
        sa.Column("selection_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("selected_by", sa.String(16), nullable=False, server_default="ACAD1"),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("status", sa.String(16), nullable=False, server_default="selected"),
        sa.Column("data_date", sa.Date(), nullable=True),
        sa.UniqueConstraint("pool_date", "strategy_id", "code",
                            name="uq_v2_acad1_pool_item"),
        sa.Index("ix_v2_acad1_pool_date", "pool_date"),
    )

    op.create_table(
        "v2_acad1_review",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("review_date", sa.Date(), nullable=False),
        sa.Column("topic", sa.String(256), nullable=False),
        sa.Column("strategy_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("verdict", sa.String(24), nullable=False, server_default="观察"),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("evidence", sa.Text(), nullable=False, server_default=""),
        sa.Column("reviewer", sa.String(16), nullable=False, server_default="ACAD1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Index("ix_v2_acad1_review_date", "review_date"),
    )


def downgrade() -> None:
    op.drop_table("v2_acad1_review")
    op.drop_table("v2_acad1_pool")
