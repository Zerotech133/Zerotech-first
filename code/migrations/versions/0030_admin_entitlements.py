"""admin entitlements: permanent/revoked/version + operation/audit (0030)

单元 B：权益永久化/撤销/乐观锁 + 管理员操作幂等表 + 逐资源审计表。

- v2_membership / v2_strategy_subscription：新增 is_permanent / revoked_at /
  revoked_by_user_id / version；订阅 valid_until 改可空（永久时为空）；status 加 'revoked'；
- v2_admin_operation：请求级幂等，唯一键 (actor_user_id, idempotency_key)；
- v2_entitlement_audit：逐资源审计，source_order_id 唯一、多索引。

安全：
- upgrade 前置 preflight（_assert_upgrade_safe）：历史异常（member 缺到期/free 带到期/
  未知 status）即拒绝，且在任何 DDL 前——绝不带异常数据强设新约束；
- downgrade 受限（_assert_downgrade_safe）：存在永久/撤销记录即拒绝，绝不有损回退。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ── preflight ──────────────────────────────────────────────────────────

def _upgrade_preflight_issues(bind) -> dict[str, list[str]]:
    issues: dict[str, list[str]] = {}

    def _ids(sql: str) -> list[str]:
        return [r[0] for r in bind.exec_driver_sql(sql).fetchall()]

    member_no_date = _ids(
        "SELECT user_id FROM v2_membership "
        "WHERE tier='member' AND valid_until IS NULL ORDER BY user_id"
    )
    if member_no_date:
        issues["membership.member_without_valid_until"] = member_no_date

    free_with_date = _ids(
        "SELECT user_id FROM v2_membership "
        "WHERE tier='free' AND valid_until IS NOT NULL ORDER BY user_id"
    )
    if free_with_date:
        issues["membership.free_with_valid_until"] = free_with_date

    m_unknown = _ids(
        "SELECT user_id FROM v2_membership "
        "WHERE status NOT IN ('active','expired') ORDER BY user_id"
    )
    if m_unknown:
        issues["membership.unknown_status"] = m_unknown

    s_unknown = _ids(
        "SELECT user_id FROM v2_strategy_subscription "
        "WHERE status NOT IN ('active','expired') ORDER BY user_id"
    )
    if s_unknown:
        issues["subscription.unknown_status"] = s_unknown

    return issues


def _assert_upgrade_safe(bind) -> None:
    issues = _upgrade_preflight_issues(bind)
    if issues:
        parts = [f"{k}: {', '.join(v)}" for k, v in sorted(issues.items())]
        raise RuntimeError(
            "0030 upgrade preflight failed（历史权益数据异常，请先修复再迁移）— "
            + "; ".join(parts)
        )


def _assert_downgrade_safe(bind) -> None:
    m = bind.exec_driver_sql(
        "SELECT count(*) FROM v2_membership "
        "WHERE is_permanent OR revoked_at IS NOT NULL"
    ).scalar()
    s = bind.exec_driver_sql(
        "SELECT count(*) FROM v2_strategy_subscription "
        "WHERE is_permanent OR revoked_at IS NOT NULL"
    ).scalar()
    if m or s:
        raise RuntimeError(
            "aborting revision 0030 downgrade：存在永久/撤销权益记录，"
            f"有损回退被拒绝（membership={m}, subscription={s}）。请用前向修复。"
        )


# ── upgrade / downgrade ────────────────────────────────────────────────

_ENTITLEMENT_TABLES = ("v2_membership", "v2_strategy_subscription")


def upgrade() -> None:
    bind = op.get_bind()
    _assert_upgrade_safe(bind)

    for table in _ENTITLEMENT_TABLES:
        op.add_column(
            table,
            sa.Column(
                "is_permanent", sa.Boolean(), nullable=False,
                server_default=sa.text("false"),
            ),
        )
        op.add_column(
            table, sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True)
        )
        op.add_column(
            table,
            sa.Column(
                "revoked_by_user_id", sa.String(64),
                sa.ForeignKey("v2_user.user_id"), nullable=True,
            ),
        )
        op.add_column(
            table,
            sa.Column(
                "version", sa.Integer(), nullable=False, server_default=sa.text("1")
            ),
        )

    # 订阅 valid_until 改可空（永久订阅为空）
    op.alter_column("v2_strategy_subscription", "valid_until", nullable=True)

    # status 约束加 'revoked'
    op.drop_constraint("ck_v2_membership_status", "v2_membership", type_="check")
    op.create_check_constraint(
        "ck_v2_membership_status", "v2_membership",
        "status IN ('active','expired','revoked')",
    )
    op.drop_constraint(
        "ck_v2_strategy_subscription_status", "v2_strategy_subscription",
        type_="check",
    )
    op.create_check_constraint(
        "ck_v2_strategy_subscription_status", "v2_strategy_subscription",
        "status IN ('active','expired','revoked')",
    )

    # 永久/撤销/版本一致性约束
    op.create_check_constraint(
        "ck_v2_membership_revocation_pair", "v2_membership",
        "(revoked_at IS NULL) = (revoked_by_user_id IS NULL)",
    )
    op.create_check_constraint(
        "ck_v2_membership_revoked_status", "v2_membership",
        "(status = 'revoked') = (revoked_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_v2_membership_version", "v2_membership", "version >= 1"
    )
    op.create_check_constraint(
        "ck_v2_strategy_subscription_term", "v2_strategy_subscription",
        "(is_permanent AND valid_until IS NULL) OR "
        "(NOT is_permanent AND valid_until IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_v2_strategy_subscription_revocation_pair", "v2_strategy_subscription",
        "(revoked_at IS NULL) = (revoked_by_user_id IS NULL)",
    )
    op.create_check_constraint(
        "ck_v2_strategy_subscription_revoked_status", "v2_strategy_subscription",
        "(status = 'revoked') = (revoked_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_v2_strategy_subscription_version", "v2_strategy_subscription",
        "version >= 1",
    )

    # 管理员操作幂等表
    op.create_table(
        "v2_admin_operation",
        sa.Column("operation_id", sa.String(64), primary_key=True),
        sa.Column(
            "actor_user_id", sa.String(64), sa.ForeignKey("v2_user.user_id"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("action", sa.String(48), nullable=False),
        sa.Column(
            "target_user_id", sa.String(64), sa.ForeignKey("v2_user.user_id"),
            nullable=False,
        ),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_body", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "actor_user_id", "idempotency_key",
            name="uq_v2_admin_operation_actor_key",
        ),
    )
    op.create_index(
        "ix_v2_admin_operation_actor", "v2_admin_operation", ["actor_user_id"]
    )
    op.create_index(
        "ix_v2_admin_operation_target", "v2_admin_operation", ["target_user_id"]
    )

    # 逐资源审计表
    op.create_table(
        "v2_entitlement_audit",
        sa.Column("audit_id", sa.String(64), primary_key=True),
        sa.Column(
            "operation_id", sa.String(64),
            sa.ForeignKey("v2_admin_operation.operation_id"), nullable=True,
        ),
        sa.Column(
            "actor_user_id", sa.String(64), sa.ForeignKey("v2_user.user_id"),
            nullable=True,
        ),
        sa.Column(
            "target_user_id", sa.String(64), sa.ForeignKey("v2_user.user_id"),
            nullable=False,
        ),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("action", sa.String(48), nullable=False),
        sa.Column("resource_type", sa.String(24), nullable=False),
        sa.Column("resource_id", sa.String(128), nullable=True),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("before_state", sa.JSON(), nullable=False),
        sa.Column("after_state", sa.JSON(), nullable=False),
        sa.Column("source_order_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "source_order_id", name="uq_v2_entitlement_audit_source_order"
        ),
    )
    op.create_index(
        "ix_v2_entitlement_audit_target", "v2_entitlement_audit",
        ["target_user_id", "created_at"],
    )
    op.create_index(
        "ix_v2_entitlement_audit_actor", "v2_entitlement_audit",
        ["actor_user_id", "created_at"],
    )
    op.create_index(
        "ix_v2_entitlement_audit_resource", "v2_entitlement_audit",
        ["resource_type", "resource_id", "created_at"],
    )
    op.create_index(
        "ix_v2_entitlement_audit_action", "v2_entitlement_audit",
        ["action", "created_at"],
    )
    op.create_index(
        "ix_v2_entitlement_audit_operation", "v2_entitlement_audit", ["operation_id"]
    )


def downgrade() -> None:
    bind = op.get_bind()
    _assert_downgrade_safe(bind)

    op.drop_table("v2_entitlement_audit")
    op.drop_table("v2_admin_operation")

    for name in (
        "ck_v2_strategy_subscription_version",
        "ck_v2_strategy_subscription_revoked_status",
        "ck_v2_strategy_subscription_revocation_pair",
        "ck_v2_strategy_subscription_term",
    ):
        op.drop_constraint(name, "v2_strategy_subscription", type_="check")
    for name in (
        "ck_v2_membership_version",
        "ck_v2_membership_revoked_status",
        "ck_v2_membership_revocation_pair",
    ):
        op.drop_constraint(name, "v2_membership", type_="check")

    # 恢复旧 status 约束（无 'revoked'）
    op.drop_constraint(
        "ck_v2_strategy_subscription_status", "v2_strategy_subscription",
        type_="check",
    )
    op.create_check_constraint(
        "ck_v2_strategy_subscription_status", "v2_strategy_subscription",
        "status IN ('active','expired')",
    )
    op.drop_constraint("ck_v2_membership_status", "v2_membership", type_="check")
    op.create_check_constraint(
        "ck_v2_membership_status", "v2_membership",
        "status IN ('active','expired')",
    )

    # 订阅 valid_until 恢复 NOT NULL（无永久行时安全，_assert_downgrade_safe 已保证）
    op.alter_column("v2_strategy_subscription", "valid_until", nullable=False)

    for table in _ENTITLEMENT_TABLES:
        op.drop_column(table, "version")
        op.drop_column(table, "revoked_by_user_id")
        op.drop_column(table, "revoked_at")
        op.drop_column(table, "is_permanent")
