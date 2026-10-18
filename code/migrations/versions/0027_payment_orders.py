"""payment orders + events (P3 BEpusdt 支付闭环)

Revision ID: 0027
Revises: 0026
Create Date: 2026-09-09

商业化改造 P3：用户自助付 USDT → BEpusdt 回调 → 自动发放会员/订阅。

- v2_payment_order：商户订单。product_type=membership/subscription，amount_usdt 为
  应付 USDT；status pending→paid（或 expired/canceled）；paid/granted 时刻分列，
  便于对账与审计。
- v2_payment_event：回调幂等去重。unique(trade_id)——同一 BEpusdt 交易的重复回调
  只处理一次，绝不重复发放权益。

规范同 0026：v2_ 前缀、FK→v2_user.user_id、DateTime(timezone=True)、显式索引、
CheckConstraint 约束枚举、upgrade/downgrade 对称可逆。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "v2_payment_order",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("order_id", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("v2_user.user_id"),
            nullable=False,
        ),
        sa.Column("product_type", sa.String(16), nullable=False),
        # membership 为 NULL；subscription 为被订阅的内置策略 strategy_id。
        sa.Column("product_ref", sa.String(64), nullable=True),
        sa.Column("months", sa.Integer(), nullable=False),
        sa.Column("amount_usdt", sa.Numeric(24, 8), nullable=False),
        sa.Column("trade_type", sa.String(20), nullable=False, server_default="usdt.trc20"),
        sa.Column("bepusdt_trade_id", sa.String(128), nullable=True),
        sa.Column("pay_address", sa.String(128), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        # 回调实收 USDT（发放依据：须 >= amount_usdt）。
        sa.Column("actual_amount", sa.Numeric(24, 8), nullable=True),
        sa.Column("notify_raw", sa.JSON(), nullable=True),
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "product_type IN ('membership','subscription')",
            name="ck_v2_payment_order_product_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending','paid','expired','canceled')",
            name="ck_v2_payment_order_status",
        ),
        sa.CheckConstraint("months >= 1", name="ck_v2_payment_order_months"),
        sa.CheckConstraint("amount_usdt > 0", name="ck_v2_payment_order_amount"),
    )
    op.create_index("ix_v2_payment_order_order_id", "v2_payment_order", ["order_id"])
    op.create_index("ix_v2_payment_order_user_id", "v2_payment_order", ["user_id"])
    op.create_index("ix_v2_payment_order_status", "v2_payment_order", ["status"])

    op.create_table(
        "v2_payment_event",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("order_id", sa.String(64), nullable=False),
        sa.Column("trade_id", sa.String(128), nullable=False, unique=True),
        sa.Column("block_txid", sa.String(128), nullable=True),
        sa.Column("raw", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_v2_payment_event_order_id", "v2_payment_event", ["order_id"])


def downgrade() -> None:
    op.drop_index("ix_v2_payment_event_order_id", table_name="v2_payment_event")
    op.drop_table("v2_payment_event")
    op.drop_index("ix_v2_payment_order_status", table_name="v2_payment_order")
    op.drop_index("ix_v2_payment_order_user_id", table_name="v2_payment_order")
    op.drop_index("ix_v2_payment_order_order_id", table_name="v2_payment_order")
    op.drop_table("v2_payment_order")
