"""account reservation ledger (Phase B Task 3)

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-07

多用户交易执行隔离 Phase B Task 3：新建 v2_account_reservation 台账表，承载两阶段资金
语义（reserve 冻结 / consume 实扣 / release 解冻）。纯 additive，不改任何 legacy 执行
路径。独立 revision，down_revision=0021（绝不追加进旧迁移）。upgrade/downgrade 对称可逆。

约束：amount > 0；status IN (RESERVED,CONSUMED,RELEASED)；reservation_id 与
idempotency_key 唯一；account_id / user_strategy_id 建 FK + 索引。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "v2_account_reservation",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("reservation_id", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "account_id",
            sa.String(64),
            sa.ForeignKey(
                "v2_user_account.account_id",
                name="fk_v2_account_reservation_account_id",
            ),
            nullable=False,
        ),
        sa.Column(
            "user_strategy_id",
            sa.String(64),
            sa.ForeignKey(
                "v2_user_strategy_instance.user_strategy_id",
                name="fk_v2_account_reservation_user_strategy_id",
            ),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("amount", sa.Numeric(24, 8), nullable=False),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="RESERVED"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_v2_account_reservation_amount"),
        sa.CheckConstraint(
            "status IN ('RESERVED','CONSUMED','RELEASED')",
            name="ck_v2_account_reservation_status",
        ),
    )
    op.create_index(
        "ix_v2_account_reservation_reservation_id",
        "v2_account_reservation",
        ["reservation_id"],
    )
    op.create_index(
        "ix_v2_account_reservation_account_id",
        "v2_account_reservation",
        ["account_id"],
    )
    op.create_index(
        "ix_v2_account_reservation_user_strategy_id",
        "v2_account_reservation",
        ["user_strategy_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_v2_account_reservation_user_strategy_id",
        table_name="v2_account_reservation",
    )
    op.drop_index(
        "ix_v2_account_reservation_account_id",
        table_name="v2_account_reservation",
    )
    op.drop_index(
        "ix_v2_account_reservation_reservation_id",
        table_name="v2_account_reservation",
    )
    op.drop_table("v2_account_reservation")
