"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-08-18
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "v2_strategy",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("strategy_id", sa.String(length=64), nullable=False),
        sa.Column("strategy_name", sa.String(length=128), nullable=False),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("strategy_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("strategy_id"),
    )
    op.create_index("ix_v2_strategy_strategy_id", "v2_strategy", ["strategy_id"])
    op.create_index("ix_v2_strategy_market", "v2_strategy", ["market"])
    op.create_index("ix_v2_strategy_status", "v2_strategy", ["status"])

    op.create_table(
        "v2_strategy_version",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("strategy_version_id", sa.String(length=64), nullable=False),
        sa.Column("strategy_id", sa.String(length=64), nullable=False),
        sa.Column("version_no", sa.String(length=32), nullable=False),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("spec", postgresql.JSONB(), nullable=False),
        sa.Column("research_ref", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["strategy_id"], ["v2_strategy.strategy_id"]),
        sa.UniqueConstraint("strategy_version_id"),
    )
    op.create_index("ix_v2_strategy_version_strategy_version_id", "v2_strategy_version", ["strategy_version_id"])
    op.create_index("ix_v2_strategy_version_strategy_id", "v2_strategy_version", ["strategy_id"])
    op.create_index("ix_v2_strategy_version_market", "v2_strategy_version", ["market"])
    op.create_index("ix_v2_strategy_version_status", "v2_strategy_version", ["status"])

    op.create_table(
        "v2_approval_request",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("order_intent_id", sa.String(length=64), nullable=False),
        sa.Column("strategy_version_id", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("ts_code", sa.String(length=32), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("qty", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("price", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("token", sa.String(length=128), nullable=False),
        sa.Column("auto_follow", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(length=64), nullable=True),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.UniqueConstraint("request_id"),
    )
    op.create_index("ix_v2_approval_request_request_id", "v2_approval_request", ["request_id"])
    op.create_index("ix_v2_approval_request_order_intent_id", "v2_approval_request", ["order_intent_id"])
    op.create_index("ix_v2_approval_request_strategy_version_id", "v2_approval_request", ["strategy_version_id"])
    op.create_index("ix_v2_approval_request_market", "v2_approval_request", ["market"])
    op.create_index("ix_v2_approval_request_status", "v2_approval_request", ["status"])

    op.create_table(
        "v2_position",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("position_id", sa.String(length=64), nullable=False),
        sa.Column("strategy_version_id", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("ts_code", sa.String(length=32), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("open_qty", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("open_price", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("close_qty", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("close_price", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("position_id"),
    )
    op.create_index("ix_v2_position_position_id", "v2_position", ["position_id"])
    op.create_index("ix_v2_position_strategy_version_id", "v2_position", ["strategy_version_id"])
    op.create_index("ix_v2_position_market", "v2_position", ["market"])
    op.create_index("ix_v2_position_ts_code", "v2_position", ["ts_code"])
    op.create_index("ix_v2_position_status", "v2_position", ["status"])

    op.create_table(
        "v2_replay",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("replay_id", sa.String(length=64), nullable=False),
        sa.Column("strategy_version_id", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("ts_code", sa.String(length=32), nullable=False),
        sa.Column("pnl", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("pnl_pct", sa.Numeric(precision=12, scale=8), nullable=False),
        sa.Column("holding_period_seconds", sa.BigInteger(), nullable=False),
        sa.Column("exit_reason", sa.String(length=32), nullable=False),
        sa.Column("attribution", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("replay_id"),
    )
    op.create_index("ix_v2_replay_replay_id", "v2_replay", ["replay_id"])
    op.create_index("ix_v2_replay_strategy_version_id", "v2_replay", ["strategy_version_id"])
    op.create_index("ix_v2_replay_market", "v2_replay", ["market"])

    op.create_table(
        "v2_instrument",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("instrument_id", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("exchange", sa.String(length=16), nullable=False),
        sa.Column("currency", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("lot_size", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("instrument_id"),
    )
    op.create_index("ix_v2_instrument_instrument_id", "v2_instrument", ["instrument_id"])
    op.create_index("ix_v2_instrument_market", "v2_instrument", ["market"])
    op.create_index("ix_v2_instrument_symbol", "v2_instrument", ["symbol"])

    op.create_table(
        "v2_bar_daily",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("instrument_id", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
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
    op.create_index("ix_v2_bar_daily_instrument_id", "v2_bar_daily", ["instrument_id"])
    op.create_index("ix_v2_bar_daily_market", "v2_bar_daily", ["market"])
    op.create_index("ix_v2_bar_daily_symbol", "v2_bar_daily", ["symbol"])
    op.create_index("ix_v2_bar_daily_trade_date", "v2_bar_daily", ["trade_date"])

    op.create_table(
        "v2_market_calendar",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("is_open", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_v2_market_calendar_market", "v2_market_calendar", ["market"])
    op.create_index("ix_v2_market_calendar_trade_date", "v2_market_calendar", ["trade_date"])


def downgrade() -> None:
    op.drop_table("v2_market_calendar")
    op.drop_table("v2_bar_daily")
    op.drop_table("v2_instrument")
    op.drop_table("v2_replay")
    op.drop_table("v2_position")
    op.drop_table("v2_approval_request")
    op.drop_table("v2_strategy_version")
    op.drop_table("v2_strategy")
