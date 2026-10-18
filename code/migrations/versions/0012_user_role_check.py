"""constrain v2_user roles

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-31
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE_CHECK = "role IN ('ADMIN', 'TRADER', 'RESEARCHER', 'RISK_MANAGER', 'VIEWER')"


def upgrade() -> None:
    op.create_check_constraint("ck_v2_user_role", "v2_user", ROLE_CHECK)


def downgrade() -> None:
    op.drop_constraint("ck_v2_user_role", "v2_user", type_="check")
