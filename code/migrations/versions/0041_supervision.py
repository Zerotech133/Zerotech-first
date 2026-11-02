"""ACAD1 老板视图：策略督导记录（0041）。"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "v2_strategy_supervision",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("supervision_date", sa.Date(), nullable=False),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("score", sa.Numeric(5, 1), nullable=True),
        sa.Column("supervisor", sa.String(32), nullable=False, server_default="ACAD1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("supervision_date", "strategy_id",
                            name="uq_v2_strategy_supervision"),
        sa.Index("ix_v2_strategy_supervision_date", "supervision_date"),
    )


def downgrade() -> None:
    op.drop_table("v2_strategy_supervision")
