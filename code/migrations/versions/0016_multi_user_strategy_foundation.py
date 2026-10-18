"""multi-user strategy foundation: add USER role to v2_user role check

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-07

本迁移是多用户策略地基（后续任务会在同一 revision 追加账户/实例/审计等表）。
Task 1 只负责角色 CHECK 约束替换：drop 旧 ck_v2_user_role，建含 USER 的新约束。
"""
from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 新六角色（含 owner-scoped USER）
ROLE_CHECK = "role IN ('ADMIN','USER','TRADER','RESEARCHER','RISK_MANAGER','VIEWER')"
# 旧五角色（0012 定义）
ROLE_CHECK_LEGACY = "role IN ('ADMIN','TRADER','RESEARCHER','RISK_MANAGER','VIEWER')"


def upgrade() -> None:
    op.drop_constraint("ck_v2_user_role", "v2_user", type_="check")
    op.create_check_constraint("ck_v2_user_role", "v2_user", ROLE_CHECK)


def downgrade() -> None:
    # 绝不把 USER 静默降级为 VIEWER：若仍有 role='USER' 的行，拒绝降级。
    bind = op.get_bind()
    user_rows = bind.execute(
        text("SELECT COUNT(*) FROM v2_user WHERE role = 'USER'")
    ).scalar_one()
    if user_rows:
        raise RuntimeError(
            f"拒绝降级：仍有 {user_rows} 个 role='USER' 的用户；"
            "请先将其改为合法的旧五角色之一，再降级到 0015。"
        )
    op.drop_constraint("ck_v2_user_role", "v2_user", type_="check")
    op.create_check_constraint("ck_v2_user_role", "v2_user", ROLE_CHECK_LEGACY)
