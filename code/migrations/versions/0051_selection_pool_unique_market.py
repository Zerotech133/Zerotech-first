"""选股池唯一键加入 market（0051）。

背景：v2_selection_pool 旧唯一键 (pool_date, code) 不含 market，跨市场同日同 code
会冲突；2026-11-05 的 00005 冲突即由此触发。本迁移只改约束，不改数据、不改列。

- upgrade：uq_v2_selection_pool_item -> (pool_date, market, code)
- downgrade：恢复 (pool_date, code)；执行前需保证库内不存在同日同 code 的跨市场重复行，
  否则 downgrade 会失败（这是预期 fail-closed，不能用静默删除解决）。
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0051"
down_revision: str | None = "0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "uq_v2_selection_pool_item"


def upgrade() -> None:
    op.execute(f'ALTER TABLE v2_selection_pool DROP CONSTRAINT IF EXISTS "{CONSTRAINT}"')
    op.execute(
        f'ALTER TABLE v2_selection_pool ADD CONSTRAINT "{CONSTRAINT}" '
        "UNIQUE (pool_date, market, code)"
    )


def downgrade() -> None:
    op.execute(f'ALTER TABLE v2_selection_pool DROP CONSTRAINT IF EXISTS "{CONSTRAINT}"')
    op.execute(
        f'ALTER TABLE v2_selection_pool ADD CONSTRAINT "{CONSTRAINT}" '
        "UNIQUE (pool_date, code)"
    )
