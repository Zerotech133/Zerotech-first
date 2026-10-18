"""add bar minute

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-18
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "v2_bar_minute",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("instrument_id", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("bar_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("high", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("low", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("close", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("volume", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("turnover", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_v2_bar_minute_instrument_id", "v2_bar_minute", ["instrument_id"])
    op.create_index("ix_v2_bar_minute_market", "v2_bar_minute", ["market"])
    op.create_index("ix_v2_bar_minute_symbol", "v2_bar_minute", ["symbol"])
    op.create_index("ix_v2_bar_minute_bar_time", "v2_bar_minute", ["bar_time"])
    op.create_index("ix_v2_bar_minute_trade_date", "v2_bar_minute", ["trade_date"])


def downgrade() -> None:
    op.drop_table("v2_bar_minute")
