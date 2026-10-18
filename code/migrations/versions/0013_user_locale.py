"""add v2_user.locale (语言偏好持久化)

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-05
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "v2_user",
        sa.Column("locale", sa.String(8), nullable=False, server_default="zh"),
    )


def downgrade() -> None:
    op.drop_column("v2_user", "locale")
