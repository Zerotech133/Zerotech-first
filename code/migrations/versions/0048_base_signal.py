"""基础信号表（买点/卖点/回避），先于 ACAD1 增强判断。

Revision ID: 0048
Revises: 0047
Create Date: 2026-11-01
"""
from alembic import op
import sqlalchemy as sa

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "v2_base_signal" in inspector.get_table_names():
        # 运行态脚本可能已建表；迁移只补索引，不重复建表。
        indexes = {i["name"] for i in inspector.get_indexes("v2_base_signal")}
        if "ix_v2_base_signal_market_state" not in indexes:
            op.create_index("ix_v2_base_signal_market_state", "v2_base_signal", ["market", "base_state"])
        if "ix_v2_base_signal_trade_date" not in indexes:
            op.create_index("ix_v2_base_signal_trade_date", "v2_base_signal", ["trade_date"])
        return
    op.create_table(
        "v2_base_signal",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128)),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("data_date", sa.Date()),
        sa.Column("strategy_id", sa.String(64)),
        sa.Column("signal_type", sa.String(128)),
        sa.Column("base_state", sa.String(16), nullable=False),
        sa.Column("trigger_rule", sa.Text()),
        sa.Column("trigger_price", sa.Numeric(20, 6)),
        sa.Column("entry_low", sa.Numeric(20, 6)),
        sa.Column("entry_high", sa.Numeric(20, 6)),
        sa.Column("stop_price", sa.Numeric(20, 6)),
        sa.Column("target_price", sa.Numeric(20, 6)),
        sa.Column("exit_rule", sa.Text()),
        sa.Column("source", sa.String(32)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("market", "code", "strategy_id", "trade_date"),
    )
    op.create_index("ix_v2_base_signal_market_state", "v2_base_signal", ["market", "base_state"])
    op.create_index("ix_v2_base_signal_trade_date", "v2_base_signal", ["trade_date"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "v2_base_signal" not in inspector.get_table_names():
        return
    op.drop_index("ix_v2_base_signal_trade_date", table_name="v2_base_signal")
    op.drop_index("ix_v2_base_signal_market_state", table_name="v2_base_signal")
    op.drop_table("v2_base_signal")
