"""每日选股中台：日报 / 选股 / 统一池 / 下游反馈（0039）。"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())

    def _ct(name, *args, **kwargs):
        if name not in existing:
            op.create_table(name, *args, **kwargs)
            existing.add(name)

    _ct(
        "v2_daily_report",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("analyst", sa.String(64), nullable=False, server_default=""),
        sa.Column("market_view", sa.Text(), nullable=False, server_default=""),
        sa.Column("yesterday_review", sa.Text(), nullable=False, server_default=""),
        sa.Column("lessons", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="submitted"),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("report_date", "strategy_id", name="uq_v2_daily_report"),
        sa.Index("ix_v2_daily_report_date", "report_date"),
    )
    _ct(
        "v2_report_pick",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("report_id", sa.BigInteger(), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False, server_default=""),
        sa.Column("signal_type", sa.String(32), nullable=False, server_default=""),
        sa.Column("score", sa.Numeric(6, 1), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.UniqueConstraint("report_id", "code", name="uq_v2_report_pick"),
        sa.Index("ix_v2_report_pick_date", "report_date"),
    )
    _ct(
        "v2_daily_pool",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("pool_date", sa.Date(), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False, server_default=""),
        sa.Column("source_strategies", sa.Text(), nullable=False, server_default=""),
        sa.Column("signal_types", sa.Text(), nullable=False, server_default=""),
        sa.Column("max_score", sa.Numeric(6, 1), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="published"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("pool_date", "code", name="uq_v2_daily_pool_item"),
        sa.Index("ix_v2_daily_pool_date", "pool_date"),
    )
    _ct(
        "v2_pool_feedback",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("pool_date", sa.Date(), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("consumer", sa.String(16), nullable=False, server_default="LUSU"),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("result", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Index("ix_v2_pool_feedback_date", "pool_date"),
    )


def downgrade() -> None:
    op.drop_table("v2_pool_feedback")
    op.drop_table("v2_daily_pool")
    op.drop_table("v2_report_pick")
    op.drop_table("v2_daily_report")
