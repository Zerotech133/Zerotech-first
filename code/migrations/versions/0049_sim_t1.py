"""sim-factor T+1 守卫字段（additive 迁移草案，本任务不 apply）。

Revision ID: 0049
Revises: 0048
Create Date: 2026-11-01

背景（P1-A，task-42）：
``api/routers/sim_factor.py`` 的手工卖出与 auto-build clear 在提交前接入统一卖出守卫
``domain/execution/t1_guard.py``。聚合持仓表 ``v2_sim_position`` 原本没有开仓时间/可卖量
字段，无法实施 CN T+1（当日买入不可卖），因此本次迁移**只加列 + 回填**，不删列、不改类型：

- ``market``：市场（默认 'CN'；sim-factor 是 CN 策略模拟台，后续多市场复用同一列）。
- ``opened_at``：最近一次建仓/加仓时间（T+1 判定；缺失时代码 fail-closed）。
- ``t1_locked_qty``：当前交易日内买入、不可卖数量；次日由 ``t1_guard`` 依据 opened_at
  自动判定过期（无需后台解锁任务），卖出只扣减可卖部分。
- 约束 ``ck_v2_sim_position_t1_locked``：``0 <= t1_locked_qty <= qty``，防止非法锁定。
- 索引 ``ix_v2_sim_position_market_opened``：按市场/开仓时间巡检。

数据回填：历史持仓 ``opened_at`` 以 ``updated_at``（最近变更）兜底；无法证明非当日的历史
数据由代码按 fail-closed 处理（卖出守卫拒绝），不会误放行当日仓。

部署顺序（重要）：本迁移与 sim_factor.py 代码需同时上线，且**先执行** ``alembic upgrade 0049``
再发布代码（路由查询新列，缺列会在部署窗口报错）。本任务只提交草案，不执行、不写库。

回滚：``alembic downgrade 0048`` 会删除 opened_at/t1_locked_qty（丢失 T+1 跟踪数据），
仅在回退 sim_factor.py 代码时使用；回退前请备份 v2_sim_position。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0049"
down_revision: str | None = "0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = ("market", "opened_at", "t1_locked_qty")
_CHECK = "ck_v2_sim_position_t1_locked"
_INDEX = "ix_v2_sim_position_market_opened"


def _existing_columns(bind: sa.engine.Connection) -> set[str]:
    insp = sa.inspect(bind)
    if "v2_sim_position" not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns("v2_sim_position")}


def upgrade() -> None:
    bind = op.get_bind()
    if "v2_sim_position" not in sa.inspect(bind).get_table_names():
        return
    cols = _existing_columns(bind)
    if "market" not in cols:
        op.add_column(
            "v2_sim_position",
            sa.Column("market", sa.String(16), nullable=False, server_default="CN"),
        )
    if "opened_at" not in cols:
        op.add_column(
            "v2_sim_position",
            sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "t1_locked_qty" not in cols:
        op.add_column(
            "v2_sim_position",
            sa.Column("t1_locked_qty", sa.Integer(), nullable=False, server_default="0"),
        )

    # 历史行回填：updated_at 兜底（保留“最近一次变更时间”语义）。仅填 NULL，可重复执行。
    op.execute(sa.text(
        "UPDATE v2_sim_position SET opened_at = COALESCE(updated_at, CURRENT_TIMESTAMP) "
        "WHERE opened_at IS NULL"
    ))

    insp = sa.inspect(bind)
    checks = {c["name"] for c in insp.get_check_constraints("v2_sim_position")}
    if _CHECK not in checks:
        op.create_check_constraint(
            _CHECK,
            "v2_sim_position",
            sa.text("t1_locked_qty >= 0 AND t1_locked_qty <= qty"),
        )
    indexes = {i["name"] for i in sa.inspect(bind).get_indexes("v2_sim_position")}
    if _INDEX not in indexes:
        op.create_index(_INDEX, "v2_sim_position", ["market", "opened_at"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "v2_sim_position" not in insp.get_table_names():
        return
    indexes = {i["name"] for i in sa.inspect(bind).get_indexes("v2_sim_position")}
    if _INDEX in indexes:
        op.drop_index(_INDEX, table_name="v2_sim_position")
    checks = {c["name"] for c in sa.inspect(bind).get_check_constraints("v2_sim_position")}
    if _CHECK in checks:
        op.drop_constraint(_CHECK, "v2_sim_position", type_="check")
    cols = _existing_columns(bind)
    for name in ("t1_locked_qty", "opened_at", "market"):
        if name in cols:
            op.drop_column("v2_sim_position", name)
