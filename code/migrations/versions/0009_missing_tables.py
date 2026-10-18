"""add missing tables

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-29

Creates 12 model tables that had no Alembic migration:
  v2_alert, v2_latest_snapshot, v2_app_state, v2_adj_factor,
  v2_earnings_forecast, v2_holder_trade, v2_top_list, v2_dividend,
  v2_market_environment, v2_promotion_decision, v2_gray_release,
  v2_backtest_run
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. v2_alert
    # ------------------------------------------------------------------
    op.create_table(
        "v2_alert",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("alert_id", sa.String(length=64), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="OPEN"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("alert_id"),
    )
    op.create_index("ix_v2_alert_alert_id", "v2_alert", ["alert_id"])
    op.create_index("ix_v2_alert_level", "v2_alert", ["level"])
    op.create_index("ix_v2_alert_status", "v2_alert", ["status"])

    # ------------------------------------------------------------------
    # 2. v2_latest_snapshot
    # ------------------------------------------------------------------
    op.create_table(
        "v2_latest_snapshot",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("snapshot_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("pre_close", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("open", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("high", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("low", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("volume", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("turnover", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("bid1", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("ask1", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="v1_sync"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("market", "symbol", name="uq_v2_snapshot_market_symbol"),
    )
    op.create_index("ix_v2_latest_snapshot_market", "v2_latest_snapshot", ["market"])
    op.create_index("ix_v2_latest_snapshot_symbol", "v2_latest_snapshot", ["symbol"])
    op.create_index("ix_v2_latest_snapshot_snapshot_time", "v2_latest_snapshot", ["snapshot_time"])

    # ------------------------------------------------------------------
    # 3. v2_app_state  (KV store — primary key is the key column itself)
    # ------------------------------------------------------------------
    op.create_table(
        "v2_app_state",
        sa.Column("key", sa.String(length=128), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ------------------------------------------------------------------
    # 4. v2_adj_factor
    # ------------------------------------------------------------------
    op.create_table(
        "v2_adj_factor",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("ts_code", sa.String(length=32), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("adj_factor", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="tushare"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ts_code", "trade_date", name="uq_v2_adj_factor_code_date"),
    )
    op.create_index("ix_v2_adj_factor_ts_code", "v2_adj_factor", ["ts_code"])
    op.create_index("ix_v2_adj_factor_trade_date", "v2_adj_factor", ["trade_date"])

    # ------------------------------------------------------------------
    # 5. v2_earnings_forecast
    # ------------------------------------------------------------------
    op.create_table(
        "v2_earnings_forecast",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("ts_code", sa.String(length=32), nullable=False),
        sa.Column("ann_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("forecast_type", sa.String(length=64), nullable=True),
        sa.Column("p_change_min", sa.Numeric(precision=20, scale=4), nullable=True),
        sa.Column("p_change_max", sa.Numeric(precision=20, scale=4), nullable=True),
        sa.Column("net_profit_min", sa.Numeric(precision=24, scale=4), nullable=True),
        sa.Column("net_profit_max", sa.Numeric(precision=24, scale=4), nullable=True),
        sa.Column("change_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ts_code", "ann_date", "end_date", name="uq_v2_forecast_key"),
    )
    op.create_index("ix_v2_earnings_forecast_ts_code", "v2_earnings_forecast", ["ts_code"])
    op.create_index("ix_v2_earnings_forecast_ann_date", "v2_earnings_forecast", ["ann_date"])

    # ------------------------------------------------------------------
    # 6. v2_holder_trade
    # ------------------------------------------------------------------
    op.create_table(
        "v2_holder_trade",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("ts_code", sa.String(length=32), nullable=False),
        sa.Column("ann_date", sa.Date(), nullable=True),
        sa.Column("holder_name", sa.String(length=128), nullable=True),
        sa.Column("holder_type", sa.String(length=8), nullable=True),
        sa.Column("in_de", sa.String(length=4), nullable=True),
        sa.Column("change_vol", sa.Numeric(precision=24, scale=4), nullable=True),
        sa.Column("change_ratio", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("after_ratio", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ts_code", "ann_date", "holder_name", "change_vol",
                            name="uq_v2_holder_trade_key"),
    )
    op.create_index("ix_v2_holder_trade_ts_code", "v2_holder_trade", ["ts_code"])
    op.create_index("ix_v2_holder_trade_ann_date", "v2_holder_trade", ["ann_date"])
    op.create_index("ix_v2_holder_trade_in_de", "v2_holder_trade", ["in_de"])

    # ------------------------------------------------------------------
    # 7. v2_top_list
    # ------------------------------------------------------------------
    op.create_table(
        "v2_top_list",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("ts_code", sa.String(length=32), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("close", sa.Numeric(precision=20, scale=4), nullable=True),
        sa.Column("pct_change", sa.Numeric(precision=20, scale=4), nullable=True),
        sa.Column("turnover_rate", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("l_buy", sa.Numeric(precision=24, scale=4), nullable=True),
        sa.Column("l_sell", sa.Numeric(precision=24, scale=4), nullable=True),
        sa.Column("net_amount", sa.Numeric(precision=24, scale=4), nullable=True),
        sa.Column("net_rate", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("reason", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ts_code", "trade_date", "reason", name="uq_v2_top_list_key"),
    )
    op.create_index("ix_v2_top_list_ts_code", "v2_top_list", ["ts_code"])
    op.create_index("ix_v2_top_list_trade_date", "v2_top_list", ["trade_date"])

    # ------------------------------------------------------------------
    # 8. v2_dividend
    # ------------------------------------------------------------------
    op.create_table(
        "v2_dividend",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("ts_code", sa.String(length=32), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("ann_date", sa.Date(), nullable=True),
        sa.Column("div_proc", sa.String(length=16), nullable=True),
        sa.Column("cash_div_tax", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("stk_div", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("record_date", sa.Date(), nullable=True),
        sa.Column("ex_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ts_code", "end_date", "ex_date", "div_proc",
                            name="uq_v2_dividend_key"),
    )
    op.create_index("ix_v2_dividend_ts_code", "v2_dividend", ["ts_code"])
    op.create_index("ix_v2_dividend_end_date", "v2_dividend", ["end_date"])
    op.create_index("ix_v2_dividend_ex_date", "v2_dividend", ["ex_date"])

    # ------------------------------------------------------------------
    # 9. v2_market_environment
    # ------------------------------------------------------------------
    op.create_table(
        "v2_market_environment",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("env_date", sa.Date(), nullable=False),
        sa.Column("regime", sa.String(length=16), nullable=False),
        sa.Column("risk_level", sa.Integer(), nullable=False),
        sa.Column("benchmark", sa.String(length=32), nullable=True),
        sa.Column("returns_5d", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("volatility_5d", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("note", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("market", "env_date", name="uq_v2_market_env_date"),
    )
    op.create_index("ix_v2_market_environment_market", "v2_market_environment", ["market"])
    op.create_index("ix_v2_market_environment_env_date", "v2_market_environment", ["env_date"])

    # ------------------------------------------------------------------
    # 10. v2_promotion_decision
    # ------------------------------------------------------------------
    op.create_table(
        "v2_promotion_decision",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("decision_id", sa.String(length=80), nullable=False),
        sa.Column("strategy_version_id", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("from_status", sa.String(length=16), nullable=True),
        sa.Column("to_status", sa.String(length=16), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("samples", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("win_rate", sa.Numeric(precision=8, scale=4), nullable=True),
        sa.Column("pnl_ratio", sa.Numeric(precision=8, scale=4), nullable=True),
        sa.Column("approver_type", sa.String(length=16), nullable=False, server_default="SYSTEM"),
        sa.Column("experiment_run_id", sa.String(length=64), nullable=True),
        sa.Column("confirm_status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("decision_id"),
    )
    op.create_index("ix_v2_promotion_decision_decision_id", "v2_promotion_decision", ["decision_id"])
    op.create_index("ix_v2_promotion_decision_strategy_version_id", "v2_promotion_decision", ["strategy_version_id"])

    # ------------------------------------------------------------------
    # 11. v2_gray_release
    # ------------------------------------------------------------------
    op.create_table(
        "v2_gray_release",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("gray_release_id", sa.String(length=80), nullable=False),
        sa.Column("strategy_version_id", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.String(length=64), nullable=False, server_default="10%仓位上限"),
        sa.Column("observation_days", sa.Integer(), nullable=False, server_default="14"),
        sa.Column("metrics_threshold", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
        sa.Column("observed_metrics", sa.JSON(), nullable=True),
        sa.Column("conclusion", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("concluded_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("gray_release_id"),
    )
    op.create_index("ix_v2_gray_release_gray_release_id", "v2_gray_release", ["gray_release_id"])
    op.create_index("ix_v2_gray_release_strategy_version_id", "v2_gray_release", ["strategy_version_id"])

    # ------------------------------------------------------------------
    # 12. v2_backtest_run
    # ------------------------------------------------------------------
    op.create_table(
        "v2_backtest_run",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("run_id", sa.String(length=80), nullable=False),
        sa.Column("strategy_version_id", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="DONE"),
        sa.Column("params", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id"),
    )
    op.create_index("ix_v2_backtest_run_run_id", "v2_backtest_run", ["run_id"])
    op.create_index("ix_v2_backtest_run_strategy_version_id", "v2_backtest_run", ["strategy_version_id"])


def downgrade() -> None:
    op.drop_table("v2_backtest_run")
    op.drop_table("v2_gray_release")
    op.drop_table("v2_promotion_decision")
    op.drop_table("v2_market_environment")
    op.drop_table("v2_dividend")
    op.drop_table("v2_top_list")
    op.drop_table("v2_holder_trade")
    op.drop_table("v2_earnings_forecast")
    op.drop_table("v2_adj_factor")
    op.drop_table("v2_app_state")
    op.drop_table("v2_latest_snapshot")
    op.drop_table("v2_alert")
