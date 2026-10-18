"""user password_changed_at (deep-test 3c)

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-08

deep-test Important 3c：v2_user 加 password_changed_at（nullable timestamptz）。
token 校验（get_current_user）用它踢掉改密前签发的旧 token：
- password_changed_at 非 NULL 且 token 无 iat（旧形态）→ 401；
- iat（秒）< password_changed_at（微秒 epoch）→ 401。

幂等设计：upgrade/downgrade 先用 inspector 查列存在性再动手——本机 dev 库
（alembic_version 停 0023）在代码合入时已手工 `ALTER TABLE v2_user ADD COLUMN
password_changed_at timestamptz` 保证 ORM 热重载不炸；该库将来 upgrade 时
0025 的 add_column 若重复执行会炸（duplicate column），故此处幂等跳过。
数据不动（NULL=从未改密，放行所有 token，语义安全）。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "v2_user"
_COLUMN = "password_changed_at"


def _has_column(bind) -> bool:
    return _COLUMN in {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind):
        return  # 幂等：dev 库手工补列/重复执行时跳过
    op.add_column(
        _TABLE,
        sa.Column(_COLUMN, sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        return
    op.drop_column(_TABLE, _COLUMN)
