"""Lu Su decision records for ACAD1 pool (0037).

One row per ACAD1 pool item. Decisions are research judgments; no execution.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "v2_lusu_decision",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("pool_date", sa.Date(), nullable=False),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False, server_default=""),
        sa.Column("verdict", sa.String(16), nullable=False),
        sa.Column("reasons", sa.Text(), nullable=False, server_default=""),
        sa.Column("risk_checks", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("position_hint", sa.String(64), nullable=False, server_default=""),
        sa.Column("decided_by", sa.String(32), nullable=False, server_default="鲁肃(D1)"),
        sa.Column("acad1_review", sa.String(32), nullable=False, server_default=""),
        sa.Column("a0_sign", sa.String(32), nullable=False, server_default=""),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("pool_date", "strategy_id", "code",
                            name="uq_v2_lusu_decision_item"),
        sa.Index("ix_v2_lusu_decision_date", "pool_date"),
    )


def downgrade() -> None:
    op.drop_table("v2_lusu_decision")
