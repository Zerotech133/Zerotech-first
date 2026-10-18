"""add missing columns to existing tables

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-29

Adds columns present in models.py that were absent from the original migrations:
  - v2_strategy.description         (String 512, nullable)
  - v2_bar_daily.pre_close          (Numeric 24/8, nullable)
  - v2_fill_record.realized_pnl     (Numeric 24/8, nullable)
  - v2_fill_record.sell_reason      (Text, nullable)
  - v2_learning_case.status         (String 16, nullable, model default REVIEWED)
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # IF NOT EXISTS 防止列已存在时报错（生产库可能在迁移记录追上来之前已有这些列）
    op.execute("ALTER TABLE v2_strategy ADD COLUMN IF NOT EXISTS description VARCHAR(512)")
    op.execute("ALTER TABLE v2_bar_daily ADD COLUMN IF NOT EXISTS pre_close NUMERIC(24,8)")
    op.execute("ALTER TABLE v2_fill_record ADD COLUMN IF NOT EXISTS realized_pnl NUMERIC(24,8)")
    op.execute("ALTER TABLE v2_fill_record ADD COLUMN IF NOT EXISTS sell_reason TEXT")
    op.execute("ALTER TABLE v2_learning_case ADD COLUMN IF NOT EXISTS status VARCHAR(16)")


def downgrade() -> None:
    op.drop_column("v2_learning_case", "status")
    op.drop_column("v2_fill_record", "sell_reason")
    op.drop_column("v2_fill_record", "realized_pnl")
    op.drop_column("v2_bar_daily", "pre_close")
    op.drop_column("v2_strategy", "description")
