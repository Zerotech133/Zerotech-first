"""add fee column to v2_position

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-30

成交手续费入账修复：v2_position 新增 fee 列（NUMERIC(24,8)，默认 0），
用于记录每个持仓批次的归属手续费（买入 lot = 全额佣金/印花税/过户费；
平仓留痕 lot = 买入比例费 + 卖出费）。pnl_amount 从此为净值（已扣双边费用）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "v2_position",
        sa.Column(
            "fee",
            sa.Numeric(precision=24, scale=8),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("v2_position", "fee")
