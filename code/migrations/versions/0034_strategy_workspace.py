"""strategy workspace sidecar tables (0034).

Additive only: external/sidecar strategies, daily picks, observations, backtest evidence.
No existing strategy tables are altered; sidecar rows are deliberately not foreign-keyed
to v2_strategy so they never leak into the paid strategy catalog.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "v2_strategy_sidecar",
        sa.Column("strategy_id", sa.String(64), primary_key=True),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="research"),
        sa.Column("owner", sa.String(64), nullable=False, server_default="STRAT1"),
        sa.Column("ops_support", sa.String(64), nullable=False, server_default=""),
        sa.Column("source_type", sa.String(32), nullable=False, server_default="script"),
        sa.Column("health", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("data_date", sa.Date(), nullable=True),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(
            "status IN ('live','shadow','research','maintenance')",
            name="ck_v2_sidecar_status",
        ),
        sa.CheckConstraint(
            "health IN ('green','yellow','red','no_task','unknown')",
            name="ck_v2_sidecar_health",
        ),
    )

    op.create_table(
        "v2_strategy_daily",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("market", sa.String(16), nullable=False, server_default="CN"),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("data_date", sa.Date(), nullable=True),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False, server_default=""),
        sa.Column("signal", sa.String(32), nullable=False, server_default=""),
        sa.Column("score", sa.Numeric(12, 4), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("snapshot_sha", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("strategy_id", "trade_date", "code",
                            name="uq_v2_strategy_daily"),
        sa.Index("ix_v2_strategy_daily_lookup", "strategy_id", "trade_date"),
    )

    op.create_table(
        "v2_strategy_observation",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("signal_date", sa.Date(), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False, server_default=""),
        sa.Column("signal", sa.String(32), nullable=False, server_default=""),
        sa.Column("entry_date", sa.Date(), nullable=True),
        sa.Column("ret_t1", sa.Numeric(12, 6), nullable=True),
        sa.Column("ret_t5", sa.Numeric(12, 6), nullable=True),
        sa.Column("ret_t10", sa.Numeric(12, 6), nullable=True),
        sa.Column("ret_t20", sa.Numeric(12, 6), nullable=True),
        sa.Column("hit", sa.Boolean(), nullable=True),
        sa.Column("max_dd", sa.Numeric(12, 6), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("strategy_id", "signal_date", "code",
                            name="uq_v2_strategy_observation"),
        sa.Index("ix_v2_strategy_obs_lookup", "strategy_id", "signal_date"),
    )

    op.create_table(
        "v2_strategy_backtest",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("window", sa.String(16), nullable=False),
        sa.Column("cost_model", sa.String(128), nullable=False, server_default=""),
        sa.Column("metrics", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("equity_path", sa.Text(), nullable=False, server_default=""),
        sa.Column("manifest_sha", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("strategy_id", "window", "cost_model",
                            name="uq_v2_strategy_backtest"),
        sa.Index("ix_v2_strategy_bt_lookup", "strategy_id", "window"),
    )


def downgrade() -> None:
    op.drop_table("v2_strategy_backtest")
    op.drop_table("v2_strategy_observation")
    op.drop_table("v2_strategy_daily")
    op.drop_table("v2_strategy_sidecar")
