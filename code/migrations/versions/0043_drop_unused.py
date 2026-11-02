"""清理 0039 未使用的重名日报表（0043）。"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    for t in ("v2_report_pick", "v2_daily_pool", "v2_pool_feedback"):
        if t in existing:
            op.drop_table(t)


def downgrade() -> None:
    pass
