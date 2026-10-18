"""add v2_user table

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-30
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "v2_user",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(64), nullable=False, unique=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="VIEWER"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_v2_user_user_id", "v2_user", ["user_id"])
    op.create_index("ix_v2_user_username", "v2_user", ["username"])


def downgrade() -> None:
    op.drop_index("ix_v2_user_username", table_name="v2_user")
    op.drop_index("ix_v2_user_user_id", table_name="v2_user")
    op.drop_table("v2_user")
