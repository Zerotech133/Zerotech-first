"""access audit (v2_access_audit)

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-07

新建 v2_access_audit 表：管理员全局访问审计（谁对哪类资源做了什么全局查看动作）。
独立 revision，down_revision=0019（绝不追加进旧迁移）。upgrade/downgrade 对称。
只落元数据（actor_user_id/action/resource_type/resource_id/request_id/created_at），
**绝不**含密码/Token/敏感响应正文——不加任意 JSON 请求/响应列。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "v2_access_audit",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("audit_id", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "actor_user_id",
            sa.String(64),
            sa.ForeignKey("v2_user.user_id"),
            nullable=False,
        ),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.String(64), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_v2_access_audit_audit_id", "v2_access_audit", ["audit_id"]
    )
    op.create_index(
        "ix_v2_access_audit_actor_created",
        "v2_access_audit",
        ["actor_user_id", "created_at"],
    )
    op.create_index(
        "ix_v2_access_audit_resource",
        "v2_access_audit",
        ["resource_type", "resource_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_v2_access_audit_resource", table_name="v2_access_audit")
    op.drop_index("ix_v2_access_audit_actor_created", table_name="v2_access_audit")
    op.drop_index("ix_v2_access_audit_audit_id", table_name="v2_access_audit")
    op.drop_table("v2_access_audit")
