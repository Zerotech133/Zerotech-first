"""allow multiple signals per code/day (0035)."""
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    op.drop_constraint("uq_v2_strategy_daily", "v2_strategy_daily", type_="unique")
    op.create_unique_constraint("uq_v2_strategy_daily_signal", "v2_strategy_daily",
                                ["strategy_id", "trade_date", "code", "signal"])

def downgrade() -> None:
    op.drop_constraint("uq_v2_strategy_daily_signal", "v2_strategy_daily", type_="unique")
    op.create_unique_constraint("uq_v2_strategy_daily", "v2_strategy_daily",
                                ["strategy_id", "trade_date", "code"])
