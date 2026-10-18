"""add v2_user.telegram_chat_id (Telegram 推送绑定)

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-06
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "v2_user",
        sa.Column("telegram_chat_id", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("v2_user", "telegram_chat_id")
