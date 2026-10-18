"""relax trade ownership NOT NULL (0028)

背景：0024 把交易链五表的 user_strategy_id/account_id 收紧为 NOT NULL（多用户隔离
enforced 世界的约束）。但产品运行在 legacy 模式：内置策略在平台侧生成**无归属**
（user_strategy_id=NULL）的平台计划，订阅者收信号——这与 NOT NULL 冲突，导致内置
策略写 v2_trade_plan 触发 NotNullViolation、整条交易链断裂（无候选/无持仓）。P2 数据
门禁本就以「owner_user_id IS NULL → 内置行」为前提，故归属可空才是 legacy+订阅模型的
正解。本迁移把 0024 的 10 处约束改回 nullable；enforced 模式仍写非空、不受影响。

纯约束放开，不动任何数据。downgrade 对称收紧（存在 NULL 行时会失败，属预期）。
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES: tuple[str, ...] = (
    "v2_trade_plan",
    "v2_approval_request",
    "v2_order_intent",
    "v2_position",
    "v2_replay",
)
_COLUMNS: tuple[str, ...] = ("user_strategy_id", "account_id")


def upgrade() -> None:
    for table in _TABLES:
        for column in _COLUMNS:
            op.alter_column(table, column, nullable=True)


def downgrade() -> None:
    # 对称收紧（存在 NULL 归属行则失败，与 0024 语义一致）。
    for table in _TABLES:
        for column in _COLUMNS:
            op.alter_column(table, column, nullable=False)
