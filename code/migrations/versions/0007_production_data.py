"""add production data tables

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-18
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. v2_candidate_pool
    # ------------------------------------------------------------------
    op.create_table(
        "v2_candidate_pool",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("pool_id", sa.String(length=64), nullable=False),
        sa.Column("strategy_version_id", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("pool_date", sa.Date(), nullable=False),
        sa.Column("pool_type", sa.String(length=32), nullable=False),
        sa.Column("batch_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_v2_candidate_pool_pool_id", "v2_candidate_pool", ["pool_id"], unique=True)
    op.create_index("ix_v2_candidate_pool_strategy_version_id", "v2_candidate_pool", ["strategy_version_id"])
    op.create_index("ix_v2_candidate_pool_market", "v2_candidate_pool", ["market"])
    op.create_index("ix_v2_candidate_pool_pool_date", "v2_candidate_pool", ["pool_date"])
    op.create_index("ix_v2_candidate_pool_status", "v2_candidate_pool", ["status"])

    # ------------------------------------------------------------------
    # 2. v2_candidate_asset
    # ------------------------------------------------------------------
    op.create_table(
        "v2_candidate_asset",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("candidate_id", sa.String(length=64), nullable=False),
        sa.Column("pool_id", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("ts_code", sa.String(length=32), nullable=False),
        sa.Column("candidate_score", sa.Numeric(12, 6), nullable=True),
        sa.Column("candidate_reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_v2_candidate_asset_candidate_id", "v2_candidate_asset", ["candidate_id"], unique=True)
    op.create_index("ix_v2_candidate_asset_pool_id", "v2_candidate_asset", ["pool_id"])
    op.create_index("ix_v2_candidate_asset_market", "v2_candidate_asset", ["market"])
    op.create_index("ix_v2_candidate_asset_ts_code", "v2_candidate_asset", ["ts_code"])
    op.create_index("ix_v2_candidate_asset_status", "v2_candidate_asset", ["status"])

    # ------------------------------------------------------------------
    # 3. v2_trade_plan
    # ------------------------------------------------------------------
    op.create_table(
        "v2_trade_plan",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("plan_id", sa.String(length=64), nullable=False),
        sa.Column("candidate_id", sa.String(length=64), nullable=False),
        sa.Column("strategy_version_id", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("ts_code", sa.String(length=32), nullable=False),
        sa.Column("plan_status", sa.String(length=32), nullable=False),
        sa.Column("ref_price", sa.Numeric(24, 8), nullable=True),
        sa.Column("stop_loss_price", sa.Numeric(24, 8), nullable=True),
        sa.Column("take_profit_price", sa.Numeric(24, 8), nullable=True),
        sa.Column("entry_conditions", JSONB(), nullable=True),
        sa.Column("exit_conditions", JSONB(), nullable=True),
        sa.Column("invalidation_conditions", JSONB(), nullable=True),
        sa.Column("risk_budget", sa.Numeric(24, 8), nullable=True),
        sa.Column("max_position_ratio", sa.Numeric(12, 6), nullable=True),
        sa.Column("confidence_level", sa.Numeric(12, 6), nullable=True),
        sa.Column("trace_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_v2_trade_plan_plan_id", "v2_trade_plan", ["plan_id"], unique=True)
    op.create_index("ix_v2_trade_plan_candidate_id", "v2_trade_plan", ["candidate_id"])
    op.create_index("ix_v2_trade_plan_strategy_version_id", "v2_trade_plan", ["strategy_version_id"])
    op.create_index("ix_v2_trade_plan_market", "v2_trade_plan", ["market"])
    op.create_index("ix_v2_trade_plan_ts_code", "v2_trade_plan", ["ts_code"])
    op.create_index("ix_v2_trade_plan_plan_status", "v2_trade_plan", ["plan_status"])

    # ------------------------------------------------------------------
    # 4. v2_order_intent
    # ------------------------------------------------------------------
    op.create_table(
        "v2_order_intent",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("intent_id", sa.String(length=64), nullable=False),
        sa.Column("plan_id", sa.String(length=64), nullable=False),
        sa.Column("strategy_version_id", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("ts_code", sa.String(length=32), nullable=False),
        sa.Column("intent_type", sa.String(length=32), nullable=False),
        sa.Column("intent_side", sa.String(length=16), nullable=False),
        sa.Column("intent_qty", sa.Numeric(24, 8), nullable=True),
        sa.Column("intent_price", sa.Numeric(24, 8), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_v2_order_intent_intent_id", "v2_order_intent", ["intent_id"], unique=True)
    op.create_index("ix_v2_order_intent_plan_id", "v2_order_intent", ["plan_id"])
    op.create_index("ix_v2_order_intent_strategy_version_id", "v2_order_intent", ["strategy_version_id"])
    op.create_index("ix_v2_order_intent_market", "v2_order_intent", ["market"])
    op.create_index("ix_v2_order_intent_ts_code", "v2_order_intent", ["ts_code"])
    op.create_index("ix_v2_order_intent_status", "v2_order_intent", ["status"])

    # ------------------------------------------------------------------
    # 5. v2_order_request
    # ------------------------------------------------------------------
    op.create_table(
        "v2_order_request",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("intent_id", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("broker_channel", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("request_payload", JSONB(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_v2_order_request_request_id", "v2_order_request", ["request_id"], unique=True)
    op.create_index("ix_v2_order_request_intent_id", "v2_order_request", ["intent_id"])
    op.create_index("ix_v2_order_request_market", "v2_order_request", ["market"])
    op.create_index("ix_v2_order_request_idempotency_key", "v2_order_request", ["idempotency_key"])
    op.create_index("ix_v2_order_request_status", "v2_order_request", ["status"])

    # ------------------------------------------------------------------
    # 6. v2_execution_record
    # ------------------------------------------------------------------
    op.create_table(
        "v2_execution_record",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("execution_id", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("broker_order_id", sa.String(length=128), nullable=True),
        sa.Column("execution_status", sa.String(length=32), nullable=False),
        sa.Column("response_payload", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_v2_execution_record_execution_id", "v2_execution_record", ["execution_id"], unique=True)
    op.create_index("ix_v2_execution_record_request_id", "v2_execution_record", ["request_id"])
    op.create_index("ix_v2_execution_record_market", "v2_execution_record", ["market"])
    op.create_index("ix_v2_execution_record_execution_status", "v2_execution_record", ["execution_status"])

    # ------------------------------------------------------------------
    # 7. v2_fill_record
    # ------------------------------------------------------------------
    op.create_table(
        "v2_fill_record",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("fill_id", sa.String(length=64), nullable=False),
        sa.Column("execution_id", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("ts_code", sa.String(length=32), nullable=False),
        sa.Column("broker_trade_id", sa.String(length=128), nullable=True),
        sa.Column("fill_price", sa.Numeric(24, 8), nullable=True),
        sa.Column("fill_qty", sa.Numeric(24, 8), nullable=True),
        sa.Column("fill_amount", sa.Numeric(24, 8), nullable=True),
        sa.Column("fill_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_v2_fill_record_fill_id", "v2_fill_record", ["fill_id"], unique=True)
    op.create_index("ix_v2_fill_record_execution_id", "v2_fill_record", ["execution_id"])
    op.create_index("ix_v2_fill_record_market", "v2_fill_record", ["market"])
    op.create_index("ix_v2_fill_record_ts_code", "v2_fill_record", ["ts_code"])

    # ------------------------------------------------------------------
    # 8. v2_risk_check
    # ------------------------------------------------------------------
    op.create_table(
        "v2_risk_check",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("check_id", sa.String(length=64), nullable=False),
        sa.Column("plan_id", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("check_stage", sa.String(length=32), nullable=False),
        sa.Column("check_round", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_v2_risk_check_check_id", "v2_risk_check", ["check_id"], unique=True)
    op.create_index("ix_v2_risk_check_plan_id", "v2_risk_check", ["plan_id"])
    op.create_index("ix_v2_risk_check_market", "v2_risk_check", ["market"])

    # ------------------------------------------------------------------
    # 9. v2_risk_decision
    # ------------------------------------------------------------------
    op.create_table(
        "v2_risk_decision",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("decision_id", sa.String(length=64), nullable=False),
        sa.Column("check_id", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("decision_result", sa.String(length=32), nullable=False),
        sa.Column("warning_flag", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("rule_results", JSONB(), nullable=True),
        sa.Column("approver_type", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_v2_risk_decision_decision_id", "v2_risk_decision", ["decision_id"], unique=True)
    op.create_index("ix_v2_risk_decision_check_id", "v2_risk_decision", ["check_id"])
    op.create_index("ix_v2_risk_decision_market", "v2_risk_decision", ["market"])
    op.create_index("ix_v2_risk_decision_decision_result", "v2_risk_decision", ["decision_result"])

    # ------------------------------------------------------------------
    # 10. v2_attribution_record
    # ------------------------------------------------------------------
    op.create_table(
        "v2_attribution_record",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("attribution_id", sa.String(length=64), nullable=False),
        sa.Column("replay_id", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("dimension", sa.String(length=64), nullable=False),
        sa.Column("weight", sa.Numeric(12, 6), nullable=True),
        sa.Column("pnl_amount", sa.Numeric(24, 8), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_v2_attribution_record_attribution_id", "v2_attribution_record", ["attribution_id"], unique=True)
    op.create_index("ix_v2_attribution_record_replay_id", "v2_attribution_record", ["replay_id"])
    op.create_index("ix_v2_attribution_record_market", "v2_attribution_record", ["market"])

    # ------------------------------------------------------------------
    # 11. v2_learning_case
    # ------------------------------------------------------------------
    op.create_table(
        "v2_learning_case",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("learning_id", sa.String(length=64), nullable=False),
        sa.Column("replay_id", sa.String(length=64), nullable=False),
        sa.Column("strategy_version_id", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("ts_code", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("pnl_pct", sa.Numeric(12, 8), nullable=True),
        sa.Column("lesson_summary", sa.Text(), nullable=True),
        sa.Column("labels", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_v2_learning_case_learning_id", "v2_learning_case", ["learning_id"], unique=True)
    op.create_index("ix_v2_learning_case_replay_id", "v2_learning_case", ["replay_id"])
    op.create_index("ix_v2_learning_case_strategy_version_id", "v2_learning_case", ["strategy_version_id"])
    op.create_index("ix_v2_learning_case_market", "v2_learning_case", ["market"])
    op.create_index("ix_v2_learning_case_ts_code", "v2_learning_case", ["ts_code"])

    # ------------------------------------------------------------------
    # 12. v2_decision_review
    # ------------------------------------------------------------------
    op.create_table(
        "v2_decision_review",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("review_id", sa.String(length=64), nullable=False),
        sa.Column("strategy_version_id", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("review_date", sa.Date(), nullable=False),
        sa.Column("decision_type", sa.String(length=32), nullable=False),
        sa.Column("ts_code", sa.String(length=32), nullable=True),
        sa.Column("score", sa.Numeric(12, 6), nullable=True),
        sa.Column("metrics", JSONB(), nullable=True),
        sa.Column("verdict", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_v2_decision_review_review_id", "v2_decision_review", ["review_id"], unique=True)
    op.create_index("ix_v2_decision_review_strategy_version_id", "v2_decision_review", ["strategy_version_id"])
    op.create_index("ix_v2_decision_review_market", "v2_decision_review", ["market"])
    op.create_index("ix_v2_decision_review_review_date", "v2_decision_review", ["review_date"])

    # ------------------------------------------------------------------
    # 13. v2_portfolio_snapshot
    # ------------------------------------------------------------------
    op.create_table(
        "v2_portfolio_snapshot",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("snapshot_id", sa.String(length=64), nullable=False),
        sa.Column("strategy_version_id", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("total_assets", sa.Numeric(24, 8), nullable=True),
        sa.Column("available_cash", sa.Numeric(24, 8), nullable=True),
        sa.Column("position_market_value", sa.Numeric(24, 8), nullable=True),
        sa.Column("position_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_v2_portfolio_snapshot_snapshot_id", "v2_portfolio_snapshot", ["snapshot_id"], unique=True)
    op.create_index("ix_v2_portfolio_snapshot_strategy_version_id", "v2_portfolio_snapshot", ["strategy_version_id"])
    op.create_index("ix_v2_portfolio_snapshot_market", "v2_portfolio_snapshot", ["market"])
    op.create_index("ix_v2_portfolio_snapshot_snapshot_date", "v2_portfolio_snapshot", ["snapshot_date"])

    # ------------------------------------------------------------------
    # 14. v2_daily_report
    # ------------------------------------------------------------------
    op.create_table(
        "v2_daily_report",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("report_id", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("report_type", sa.String(length=32), nullable=False),
        sa.Column("facts", JSONB(), nullable=True),
        sa.Column("narrative", sa.Text(), nullable=True),
        sa.Column("model_used", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_v2_daily_report_report_id", "v2_daily_report", ["report_id"], unique=True)
    op.create_index("ix_v2_daily_report_market", "v2_daily_report", ["market"])
    op.create_index("ix_v2_daily_report_report_date", "v2_daily_report", ["report_date"])

    # ------------------------------------------------------------------
    # 15. v2_experiment_run
    # ------------------------------------------------------------------
    op.create_table(
        "v2_experiment_run",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("experiment_id", sa.String(length=64), nullable=False),
        sa.Column("strategy_version_id", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("config", JSONB(), nullable=True),
        sa.Column("result", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_v2_experiment_run_experiment_id", "v2_experiment_run", ["experiment_id"], unique=True)
    op.create_index("ix_v2_experiment_run_strategy_version_id", "v2_experiment_run", ["strategy_version_id"])
    op.create_index("ix_v2_experiment_run_market", "v2_experiment_run", ["market"])
    op.create_index("ix_v2_experiment_run_status", "v2_experiment_run", ["status"])

    # ------------------------------------------------------------------
    # Align v2_position: add new nullable columns
    # ------------------------------------------------------------------
    op.add_column("v2_position", sa.Column("trade_plan_id", sa.String(length=64), nullable=True))
    op.add_column("v2_position", sa.Column("avg_cost", sa.Numeric(24, 8), nullable=True))
    op.add_column("v2_position", sa.Column("pnl_amount", sa.Numeric(24, 8), nullable=True))
    op.add_column("v2_position", sa.Column("pnl_pct", sa.Numeric(12, 8), nullable=True))
    op.add_column("v2_position", sa.Column("stop_loss_price", sa.Numeric(24, 8), nullable=True))
    op.create_index("ix_v2_position_trade_plan_id", "v2_position", ["trade_plan_id"])

    # ------------------------------------------------------------------
    # Align v2_replay: add new nullable columns
    # ------------------------------------------------------------------
    op.add_column("v2_replay", sa.Column("open_date", sa.Date(), nullable=True))
    op.add_column("v2_replay", sa.Column("close_date", sa.Date(), nullable=True))
    op.add_column("v2_replay", sa.Column("qty", sa.Numeric(24, 8), nullable=True))
    op.add_column("v2_replay", sa.Column("open_price", sa.Numeric(24, 8), nullable=True))
    op.add_column("v2_replay", sa.Column("close_price", sa.Numeric(24, 8), nullable=True))
    op.add_column("v2_replay", sa.Column("review_status", sa.String(length=32), nullable=True))
    op.add_column("v2_replay", sa.Column("outcome", sa.String(length=32), nullable=True))


def downgrade() -> None:
    # ------------------------------------------------------------------
    # Revert v2_replay extra columns
    # ------------------------------------------------------------------
    op.drop_column("v2_replay", "outcome")
    op.drop_column("v2_replay", "review_status")
    op.drop_column("v2_replay", "close_price")
    op.drop_column("v2_replay", "open_price")
    op.drop_column("v2_replay", "qty")
    op.drop_column("v2_replay", "close_date")
    op.drop_column("v2_replay", "open_date")

    # ------------------------------------------------------------------
    # Revert v2_position extra columns
    # ------------------------------------------------------------------
    op.drop_index("ix_v2_position_trade_plan_id", table_name="v2_position")
    op.drop_column("v2_position", "stop_loss_price")
    op.drop_column("v2_position", "pnl_pct")
    op.drop_column("v2_position", "pnl_amount")
    op.drop_column("v2_position", "avg_cost")
    op.drop_column("v2_position", "trade_plan_id")

    # ------------------------------------------------------------------
    # Drop 15 new tables (reverse order)
    # ------------------------------------------------------------------
    op.drop_table("v2_experiment_run")
    op.drop_table("v2_daily_report")
    op.drop_table("v2_portfolio_snapshot")
    op.drop_table("v2_decision_review")
    op.drop_table("v2_learning_case")
    op.drop_table("v2_attribution_record")
    op.drop_table("v2_risk_decision")
    op.drop_table("v2_risk_check")
    op.drop_table("v2_fill_record")
    op.drop_table("v2_execution_record")
    op.drop_table("v2_order_request")
    op.drop_table("v2_order_intent")
    op.drop_table("v2_trade_plan")
    op.drop_table("v2_candidate_asset")
    op.drop_table("v2_candidate_pool")
