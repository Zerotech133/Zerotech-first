"""add concepts

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-18
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "v2_concept",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("concept_code", sa.String(length=32), nullable=False),
        sa.Column("concept_name", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_v2_concept_concept_code", "v2_concept", ["concept_code"])
    op.create_table(
        "v2_concept_membership",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("concept_id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_v2_concept_membership_concept_id", "v2_concept_membership", ["concept_id"])
    op.create_index("ix_v2_concept_membership_symbol", "v2_concept_membership", ["symbol"])


def downgrade() -> None:
    op.drop_table("v2_concept_membership")
    op.drop_table("v2_concept")
