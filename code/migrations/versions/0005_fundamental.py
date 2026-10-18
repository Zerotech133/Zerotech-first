"""add fundamental

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-18
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "v2_fundamental",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("ann_date", sa.Date(), nullable=True),
        sa.Column("roe", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("roe_dt", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("debt_to_assets", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("netprofit_yoy", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("profit_dedt_cny", sa.Numeric(precision=24, scale=4), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_v2_fundamental_symbol", "v2_fundamental", ["symbol"])
    op.create_index("ix_v2_fundamental_market", "v2_fundamental", ["market"])
    op.create_index("ix_v2_fundamental_end_date", "v2_fundamental", ["end_date"])


def downgrade() -> None:
    op.drop_table("v2_fundamental")
