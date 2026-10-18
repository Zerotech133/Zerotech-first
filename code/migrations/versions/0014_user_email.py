"""add v2_user.email (公开注册邮箱, 唯一)

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-05
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # nullable：现有用户(admin 等)无邮箱；注册端点强制非空。
    op.add_column(
        "v2_user",
        sa.Column("email", sa.String(255), nullable=True),
    )
    # 唯一索引（PG 允许多个 NULL 并存，仅对已填邮箱去重）。
    op.create_index("uq_v2_user_email", "v2_user", ["email"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_v2_user_email", table_name="v2_user")
    op.drop_column("v2_user", "email")
