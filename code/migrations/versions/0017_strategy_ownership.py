"""strategy ownership + publication request

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-07

给 v2_strategy 加归属/可见性/发布态三列（现有行按 server_default 回填为平台
PUBLIC/PUBLISHED），并新建 v2_strategy_publication_request 表（私有→公共发布申请）。
独立 revision，down_revision=0016（绝不追加进 0016）。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- v2_strategy 归属/可见性/发布态（additive，现有行回填平台默认）---
    op.add_column(
        "v2_strategy", sa.Column("owner_user_id", sa.String(64), nullable=True)
    )
    op.add_column(
        "v2_strategy",
        sa.Column(
            "visibility", sa.String(16), nullable=False, server_default="PUBLIC"
        ),
    )
    op.add_column(
        "v2_strategy",
        sa.Column(
            "publication_status",
            sa.String(24),
            nullable=False,
            server_default="PUBLISHED",
        ),
    )
    op.create_index("ix_v2_strategy_owner", "v2_strategy", ["owner_user_id"])
    op.create_check_constraint(
        "ck_v2_strategy_visibility", "v2_strategy", "visibility IN ('PUBLIC','PRIVATE')"
    )
    op.create_check_constraint(
        "ck_v2_strategy_publication",
        "v2_strategy",
        "publication_status IN ('PRIVATE','PENDING_REVIEW','PUBLISHED','REJECTED')",
    )

    # --- v2_strategy_publication_request ---
    op.create_table(
        "v2_strategy_publication_request",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("request_id", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "source_strategy_id",
            sa.String(64),
            sa.ForeignKey("v2_strategy.strategy_id"),
            nullable=False,
        ),
        sa.Column(
            "requester_user_id",
            sa.String(64),
            sa.ForeignKey("v2_user.user_id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column(
            "reviewed_by", sa.String(64), sa.ForeignKey("v2_user.user_id"), nullable=True
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "derived_strategy_id",
            sa.String(64),
            sa.ForeignKey("v2_strategy.strategy_id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING','APPROVED','REJECTED')",
            name="ck_v2_publication_status",
        ),
    )
    op.create_index(
        "ix_v2_publication_request_id",
        "v2_strategy_publication_request",
        ["request_id"],
    )
    op.create_index(
        "ix_v2_publication_source",
        "v2_strategy_publication_request",
        ["source_strategy_id"],
    )
    op.create_index(
        "ix_v2_publication_requester",
        "v2_strategy_publication_request",
        ["requester_user_id"],
    )
    op.create_index(
        "ix_v2_publication_status",
        "v2_strategy_publication_request",
        ["status"],
    )
    # 同一 source 最多一条 PENDING 申请（PostgreSQL 部分唯一索引）。
    op.create_index(
        "uq_v2_publication_pending_source",
        "v2_strategy_publication_request",
        ["source_strategy_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )


def downgrade() -> None:
    op.drop_table("v2_strategy_publication_request")
    op.drop_constraint("ck_v2_strategy_publication", "v2_strategy", type_="check")
    op.drop_constraint("ck_v2_strategy_visibility", "v2_strategy", type_="check")
    op.drop_index("ix_v2_strategy_owner", table_name="v2_strategy")
    op.drop_column("v2_strategy", "publication_status")
    op.drop_column("v2_strategy", "visibility")
    op.drop_column("v2_strategy", "owner_user_id")
