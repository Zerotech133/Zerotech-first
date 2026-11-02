"""选股中台按市场拆分：report/pick/pool 增 market（0044）。"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044"
down_revision: str | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("v2_selection_report", "v2_selection_pick", "v2_selection_pool"):
        op.add_column(table, sa.Column("market", sa.String(16), nullable=False,
                                       server_default="CN"))
        op.create_index("ix_%s_market" % table, table, ["market"])


def downgrade() -> None:
    for table in ("v2_selection_pool", "v2_selection_pick", "v2_selection_report"):
        op.drop_index("ix_%s_market" % table, table_name=table)
        op.drop_column(table, "market")
