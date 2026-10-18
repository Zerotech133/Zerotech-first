"""add v2_user.auth_version (session revocation generation) (0029)

单元 A：为每个用户增加会话吊销代数 auth_version。签发的 token 携带该值；角色变更、
账户停用、改密时递增，使旧 token 立即失效（见 api/dependencies.resolve_token_identity）。

- NOT NULL DEFAULT 1：旧行由常量默认回填 1（缺 claim 的旧 token 仅在版本仍为 1 时兼容）；
- 前向安全迁移：downgrade 显式拒绝——绝不删除已递增的安全代数列，故障采用前向修复。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "v2_user",
        sa.Column(
            "auth_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )


def downgrade() -> None:
    raise RuntimeError(
        "0029 auth_version is forward-only; keep the security version column "
        "and apply a forward fix"
    )
