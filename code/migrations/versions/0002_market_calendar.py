"""add market calendar

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-18

NOTE: v2_market_calendar was already created in revision 0001 (with BigInteger id).
This migration is intentionally a no-op to preserve the revision chain.
The Integer vs BigInteger difference is immaterial — PostgreSQL BIGINT satisfies
all Integer ORM mappings.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # v2_market_calendar already exists from revision 0001; nothing to do.
    pass


def downgrade() -> None:
    # Nothing was created in this revision; nothing to undo.
    pass
