"""payment grant status machine: grant_* / snapshot / product_ref FK + backfill (0031)

单元 C：支付发放状态机与补偿。

- v2_payment_order 增发放状态机字段（grant_status / attempts / lease / retry / 复核 / 快照）；
  product_ref 加对 v2_strategy.strategy_id 的可空外键（RESTRICT，非级联）；
- 回填：granted_at 非空→granted；status=paid 且未 granted→manual_review + historical_grant_ambiguous
  （历史歧义，须人工复核，不自动发放）；快照从当前策略(subscription)/固定名(membership)回填；
- upgrade preflight：subscription 订单 product_ref 孤儿/为 NULL、membership 订单带 product_ref
  即拒（历史脏数据不带入新约束），且在任何 DDL 前；
- downgrade 受限：存在 processing/retrying/failed/manual_review 或 historical_grant_ambiguous 即拒，
  绝不有损回退（发放状态机产生的复核/失败态须前向修复）。
DDL 本体（FK/CHECK/字段）由真实 PostgreSQL rehearsal 验证；单测覆盖 preflight/回填/downgrade 断言。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GRANT_STATUSES = ("pending", "processing", "retrying", "granted", "failed", "manual_review")


# ── preflight ──────────────────────────────────────────────────────────

def _upgrade_preflight_issues(bind) -> dict[str, list[str]]:
    issues: dict[str, list[str]] = {}

    def _ids(sql: str) -> list[str]:
        return [r[0] for r in bind.exec_driver_sql(sql).fetchall()]

    orphan = _ids(
        "SELECT order_id FROM v2_payment_order "
        "WHERE product_type='subscription' AND product_ref IS NOT NULL "
        "AND product_ref NOT IN (SELECT strategy_id FROM v2_strategy) "
        "ORDER BY order_id"
    )
    if orphan:
        issues["order.subscription_product_ref_orphan"] = orphan

    noref = _ids(
        "SELECT order_id FROM v2_payment_order "
        "WHERE product_type='subscription' AND product_ref IS NULL ORDER BY order_id"
    )
    if noref:
        issues["order.subscription_without_product_ref"] = noref

    mref = _ids(
        "SELECT order_id FROM v2_payment_order "
        "WHERE product_type='membership' AND product_ref IS NOT NULL ORDER BY order_id"
    )
    if mref:
        issues["order.membership_with_product_ref"] = mref

    return issues


def _assert_upgrade_safe(bind) -> None:
    issues = _upgrade_preflight_issues(bind)
    if issues:
        parts = [f"{k}: {', '.join(v)}" for k, v in sorted(issues.items())]
        raise RuntimeError(
            "0031 upgrade preflight failed（历史支付订单数据异常，请先修复再迁移）— "
            + "; ".join(parts)
        )


def _assert_downgrade_safe(bind) -> None:
    n = bind.exec_driver_sql(
        "SELECT count(*) FROM v2_payment_order "
        "WHERE grant_status NOT IN ('pending','granted') "
        "OR historical_grant_ambiguous"
    ).scalar()
    if n:
        raise RuntimeError(
            "aborting revision 0031 downgrade：存在处理中/失败/待复核/历史歧义订单，"
            f"有损回退被拒绝（count={n}）。请用前向修复。"
        )


# ── backfill ───────────────────────────────────────────────────────────

def _backfill(bind) -> None:
    """历史订单回填发放状态与产品快照（跨 sqlite/pg）。"""
    true_lit = "1" if bind.dialect.name == "sqlite" else "true"

    # 已发放（granted_at 非空）→ granted
    bind.exec_driver_sql(
        "UPDATE v2_payment_order SET grant_status='granted' WHERE granted_at IS NOT NULL"
    )
    # 历史 paid 但未 granted → 归属不明，须人工复核，不自动发放
    bind.exec_driver_sql(
        "UPDATE v2_payment_order SET grant_status='manual_review', "
        f"historical_grant_ambiguous={true_lit}, next_grant_retry_at=NULL "
        "WHERE status='paid' AND granted_at IS NULL"
    )
    # 快照：subscription 从当前策略回填名称/市场
    bind.exec_driver_sql(
        "UPDATE v2_payment_order SET "
        "product_name_snapshot=COALESCE("
        "(SELECT strategy_name FROM v2_strategy WHERE strategy_id=v2_payment_order.product_ref), "
        "product_name_snapshot), "
        "market_snapshot=("
        "SELECT market FROM v2_strategy WHERE strategy_id=v2_payment_order.product_ref) "
        "WHERE product_type='subscription'"
    )
    # 快照：membership 用固定产品名
    bind.exec_driver_sql(
        "UPDATE v2_payment_order SET product_name_snapshot='membership' "
        "WHERE product_type='membership' "
        "AND (product_name_snapshot IS NULL OR product_name_snapshot='')"
    )


# ── upgrade / downgrade ────────────────────────────────────────────────

def upgrade() -> None:
    bind = op.get_bind()
    _assert_upgrade_safe(bind)

    op.add_column(
        "v2_payment_order",
        sa.Column("grant_status", sa.String(16), nullable=False,
                  server_default="pending"),
    )
    op.add_column(
        "v2_payment_order",
        sa.Column("grant_attempts", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
    )
    op.add_column(
        "v2_payment_order",
        sa.Column("automatic_grant_attempts", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
    )
    op.add_column(
        "v2_payment_order",
        sa.Column("next_grant_retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "v2_payment_order",
        sa.Column("last_grant_error_code", sa.String(64), nullable=True),
    )
    op.add_column(
        "v2_payment_order",
        sa.Column("last_grant_error_summary", sa.String(500), nullable=True),
    )
    op.add_column(
        "v2_payment_order",
        sa.Column("grant_lease_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "v2_payment_order",
        sa.Column("grant_lease_owner", sa.String(64), nullable=True),
    )
    op.add_column(
        "v2_payment_order",
        sa.Column("historical_grant_ambiguous", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
    )
    op.add_column(
        "v2_payment_order",
        sa.Column("grant_reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "v2_payment_order",
        sa.Column("grant_reviewed_by_user_id", sa.String(64),
                  sa.ForeignKey("v2_user.user_id"), nullable=True),
    )
    op.add_column(
        "v2_payment_order",
        sa.Column("grant_review_decision", sa.String(16), nullable=True),
    )
    op.add_column(
        "v2_payment_order",
        sa.Column("product_name_snapshot", sa.String(128), nullable=False,
                  server_default=""),
    )
    op.add_column(
        "v2_payment_order",
        sa.Column("market_snapshot", sa.String(16), nullable=True),
    )

    # product_ref → v2_strategy.strategy_id（可空，非级联=RESTRICT；preflight 已保证无孤儿）
    op.create_foreign_key(
        "fk_v2_payment_order_product_ref", "v2_payment_order", "v2_strategy",
        ["product_ref"], ["strategy_id"],
    )

    op.create_check_constraint(
        "ck_v2_payment_order_grant_status", "v2_payment_order",
        "grant_status IN " + str(_GRANT_STATUSES),
    )
    op.create_check_constraint(
        "ck_v2_payment_order_grant_attempts", "v2_payment_order",
        "grant_attempts >= 0 AND automatic_grant_attempts >= 0",
    )
    op.create_check_constraint(
        "ck_v2_payment_order_product_ref_pairing", "v2_payment_order",
        "(product_type='membership' AND product_ref IS NULL) OR "
        "(product_type='subscription' AND product_ref IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_v2_payment_order_grant_review_decision", "v2_payment_order",
        "grant_review_decision IS NULL OR "
        "grant_review_decision IN ('RETRY','MARK_GRANTED')",
    )
    op.create_check_constraint(
        "ck_v2_payment_order_review_pair", "v2_payment_order",
        "(grant_reviewed_at IS NULL) = (grant_reviewed_by_user_id IS NULL)",
    )

    op.create_index(
        "ix_v2_payment_order_grant_queue", "v2_payment_order",
        ["grant_status", "next_grant_retry_at"],
    )

    _backfill(bind)


def downgrade() -> None:
    bind = op.get_bind()
    _assert_downgrade_safe(bind)

    op.drop_index("ix_v2_payment_order_grant_queue", "v2_payment_order")
    for name in (
        "ck_v2_payment_order_review_pair",
        "ck_v2_payment_order_grant_review_decision",
        "ck_v2_payment_order_product_ref_pairing",
        "ck_v2_payment_order_grant_attempts",
        "ck_v2_payment_order_grant_status",
    ):
        op.drop_constraint(name, "v2_payment_order", type_="check")
    op.drop_constraint(
        "fk_v2_payment_order_product_ref", "v2_payment_order", type_="foreignkey"
    )
    for col in (
        "market_snapshot",
        "product_name_snapshot",
        "grant_review_decision",
        "grant_reviewed_by_user_id",
        "grant_reviewed_at",
        "historical_grant_ambiguous",
        "grant_lease_owner",
        "grant_lease_until",
        "last_grant_error_summary",
        "last_grant_error_code",
        "next_grant_retry_at",
        "automatic_grant_attempts",
        "grant_attempts",
        "grant_status",
    ):
        op.drop_column("v2_payment_order", col)
